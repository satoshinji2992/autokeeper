"""Collected LAN declarations reach one keeper arrangement before individual rulings."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from autokeeper import paths
from autokeeper.agents.cast import Action, PartyPlan, PartyStep, Plan, Ruling
from autokeeper.engine.encounter.model import EncounterState
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, closed, hold_preparation, opened, pool_open, until


class PartyCast(TableCast):
    def __init__(self, *, fail: bool = False, fail_telling: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.fail_telling = fail_telling
        self.batches: list[list[tuple[str, str, str]]] = []
        self.individual_plans: list[str] = []
        self.ruled: list[tuple[str, str]] = []
        self.coordinations: list[tuple[str, str]] = []
        self.reports: list[str] = []

    @staticmethod
    def action(text: str) -> Action:
        if "拖住" in text:
            return Action(kind="talk", summary="和布道者交谈，试着让他暂留在这里。", npc="preacher", speech="先生，能聊聊钟声吗？")
        return Action(kind="investigate", summary=text, target="钟楼门锁" if "开锁" in text else "旧报纸")

    async def party_plan(self, state, inputs, *, check=None):
        self.batches.append([(i.token, i.who, i.text) for i in inputs])
        if self.fail:
            raise RuntimeError("temporary planning failure")
        # The distraction is declared later but must be considered before the lock attempt.
        ordered = sorted(inputs, key=lambda i: "拖住" not in i.text)
        steps = []
        for i in ordered:
            related = [other.token for other in inputs if other.token != i.token]
            steps.append(PartyStep(token=i.token, plan=Plan(action=self.action(i.text)), related=related,
                                   coordination="一人交谈拖住布道者，另一人尝试开锁；效果仍待各自行动结算。"))
        return PartyPlan(steps=steps)

    async def plan(self, state, text, **kwargs):
        self.individual_plans.append(text)
        return Plan(action=self.action(text))

    async def arbiter(self, state, action, at=None, *, coordination="", **kwargs):
        self.ruled.append((state.investigator.id, action.summary))
        self.coordinations.append((state.investigator.id, coordination))
        return Ruling(needs_roll=False, minutes=30 if "开锁" in action.summary else 10)

    async def keeper(self, state, report, on_delta=None, lore=""):
        if self.fail_telling:
            self.fail_telling = False
            raise ConnectionError("temporary narration failure")
        self.reports.append(report)
        return await super().keeper(state, report, on_delta, lore)


def make_hub(fake, cast=None, *, with_ai=False):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    party = [lead, friend]
    if with_ai:
        party.append(lead.model_copy(update={"id": "chen", "name": "陈明"}, deep=True))
    st = Game.new(m, party, slot="party-planning", human=[lead.id, friend.id])
    st.add("keeper", "你们站在广场边。", who="守秘人")
    return Hub(EXAMPLE, state=st, cast=cast or PartyCast(), rules=Rules(dice=fake))


async def test_collective_arrangement_precedes_every_ruling_and_reuses_each_plan(fake, monkeypatch):
    cast = PartyCast()
    hub = make_hub(fake, cast)
    # Put the lock attempt in first, just as a faster network connection could.
    hub.pool = [("zhouheng", "我趁他没注意尝试开锁"), ("lin", "我和布道者聊聊，拖住他")]
    hub.sits = {"zhouheng": "a", "lin": "b"}
    events = []

    async def event(ev):
        events.append(ev)

    monkeypatch.setattr(hub, "_event", event)
    before = hub.state.minutes
    await hub._settle()
    assert cast.batches == [[("0", "zhouheng", hub.pool[0][1]), ("1", "lin", hub.pool[1][1])]]
    assert [who for who, _ in cast.ruled] == ["lin", "zhouheng"]
    assert cast.individual_plans == []  # one batch replaces two individual planning calls
    assert hub.state.minutes - before == 30  # simultaneous elapsed time remains the longest action
    assert len(cast.reports) == 1  # the NPC dialogue was already delivered, so it is not told again
    assert len([ev for ev in events if ev["type"] == "npc" and ev["npc"] == "preacher"]) == 1
    assert not hub.state.held and hub.state.round_spent is None
    assert [(e.who, e.text) for e in hub.state.log if e.kind == "player"] == [
        ("林秋", hub.pool[1][1]), ("周衡", hub.pool[0][1]),
    ]  # the model's neutral plan never replaces a player's original declaration


async def test_the_batch_is_available_before_the_first_arbiter_call(fake):
    class Checks(PartyCast):
        async def arbiter(self, state, action, at=None, **kwargs):
            assert len(self.batches) == 1 and len(self.batches[0]) == 2
            return await super().arbiter(state, action, at, **kwargs)

    cast = Checks()
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    await hub._settle()
    assert len(cast.ruled) == 2


async def test_related_rulings_receive_the_neutral_attempt_and_actual_completion_status(fake):
    cast = PartyCast()
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    await hub._settle()
    first, second = [context for _, context in cast.coordinations]
    assert "尚待结算" in first and "已结算" in second
    assert "试着让他暂留在这里" in second
    assert "先生，能聊聊钟声吗？" not in second  # another player's verbatim dialogue does not accompany the relation
    assert "我拖住布道者" not in second


async def test_one_player_cannot_reserve_two_declaration_slots(fake):
    from autokeeper.engine.game import PreparationError

    cast = PartyCast()
    hub = make_hub(fake, cast)
    order = [("zhouheng", "我试着开锁"), ("zhouheng", "我看看旧报纸"), ("lin", "我拖住布道者")]
    before = hub.state.minutes
    with pytest.raises(PreparationError):
        await hub.game.prepare_party(order)
    assert not cast.batches and not cast.ruled and not hub.state.round_plan
    assert hub.state.minutes == before


async def test_a_batch_planning_failure_preserves_the_individual_path(fake):
    cast = PartyCast(fail=True)
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    await hub._settle()
    assert len(cast.batches) == 1 and len(cast.ruled) == 2
    assert cast.individual_plans == [text for _, text in hub.pool]
    assert hub.state.round_spent is None and not hub.state.pending_turn


async def test_a_single_declaration_does_not_wait_for_a_missing_player(fake):
    cast = PartyCast()
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁")]
    await hub._settle()
    assert cast.batches == [[("0", "zhouheng", "我试着开锁")]] and len(cast.ruled) == 1
    assert cast.individual_plans == []


async def test_a_cast_without_party_planning_keeps_the_existing_network_path(fake):
    cast = TableCast()
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "看看旧报纸"), ("lin", "看看门窗")]
    await hub._settle()
    assert [e.text for e in hub.state.log if e.kind == "player"] == [text for _, text in hub.pool]
    assert hub.state.round_spent is None


async def test_undeclared_ai_actions_keep_their_existing_path_after_the_known_batch(fake):
    cast = PartyCast()
    hub = make_hub(fake, cast, with_ai=True)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    await hub._settle()
    assert len(cast.batches) == 1 and {who for _, who, _ in cast.batches[0]} == {"zhouheng", "lin"}
    assert cast.ai_turns == ["chen"] and cast.individual_plans == ["我四下看看"]
    assert [who for who, _ in cast.ruled] == ["lin", "zhouheng", "chen"]


async def test_cancelling_a_round_arrangement_still_closes_the_shared_time_window(fake, monkeypatch):
    hub = make_hub(fake)
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]

    async def cancelled(_order):
        raise asyncio.CancelledError

    monkeypatch.setattr(hub.game, "arrange_party", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await hub._settle()
    assert hub.state.round_spent is None and not hub.game.holding


def test_the_collective_plan_receipt_never_reaches_player_state(fake):
    hub = make_hub(fake)
    hub.state.round_plan = {"inputs": [["lin", "私下试探他的身份"]], "steps": [{"coordination": "幕后布置待演"}]}
    hub.state.round_elapsed = {"lin": 10}
    assert "round_plan" not in hub._view("lin")
    assert "round_elapsed" not in hub._view("lin")
    assert "幕后布置待演" not in json.dumps(hub._view(), ensure_ascii=False)


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_encounters_keep_their_own_round_arrangement(fake, monkeypatch, mode):
    cast = PartyCast()
    hub = make_hub(fake, cast)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "冲上去"), ("lin", "退到门后")]
    hub.state.encounter = EncounterState(id="existing", mode=mode, place=hub.state.location, started_turn=0,
                                         participants=[])
    hub.state.round_plan["encounter"] = ["existing", mode, 1]  # this is a freshly confirmed encounter batch
    settled = []

    async def run(text, inv_id, **kwargs):
        settled.append((inv_id, text))

    monkeypatch.setattr(hub, "_run", run)
    await hub._settle()
    assert cast.batches == [] and settled == hub.pool


@pytest.mark.parametrize("resume", [False, True])
async def test_real_action_window_passes_both_declarations_to_one_plan(fake, monkeypatch, resume):
    cast = PartyCast(fail_telling=resume)
    hub = make_hub(fake, cast)
    ready = hold_preparation(monkeypatch, hub.game)
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = [await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws") for _ in range(2)]
            try:
                for ws, name, seat in ((a, "甲", "zhouheng"), (b, "乙", "lin")):
                    await ws.send_json({"t": "hello", "name": name})
                    await until(ws, lambda x: x["t"] == "welcome")
                    await ws.send_json({"t": "sit", "investigator": seat})
                await until(a, pool_open)
                await a.send_json({"t": "act", "text": "我试着开锁"})
                await b.send_json({"t": "act", "text": "我拖住布道者"})
                await a.send_json({"t": "done"})
                await b.send_json({"t": "done"})
                mine = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said"
                                   and x["ev"]["who"] == "周衡")
                assert mine["ev"]["who"] == "周衡" and "audience" not in mine["ev"]
                await until(b, lambda x: x["t"] == "pool" and x["count"] == 2)
                ready.set()
                first = await until(a, lambda x: x["t"] == "turn")
                assert first["investigator"] == "lin"  # the selected batch order, independent of private declaration echoes
                if resume:
                    error = await until(a, lambda x: x["t"] == "error" and x.get("retry"))
                    assert error and len(cast.ruled) == 2 and len(cast.batches) == 1
                    minute = hub.state.minutes
                    await a.send_json({"t": "act", "text": "/retry"})
                await until(a, pool_open)
                assert len(cast.batches) == 1 and len(cast.ruled) == 2 and not cast.individual_plans
                assert [who for who, _ in cast.ruled] == ["lin", "zhouheng"]
                if resume:
                    assert hub.state.minutes == minute and not hub.state.pending_turn
            finally:
                await asyncio.gather(a.close(), b.close())
    finally:
        await closed(task)


async def test_a_planned_window_cancelled_before_execution_keeps_its_saved_clock(fake, monkeypatch):
    hub = make_hub(fake)
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    before = hub.state.minutes

    async def cancelled(_order):
        raise asyncio.CancelledError

    monkeypatch.setattr(hub, "_settle_round", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await hub._settle()
    hub._save()
    saved = GameState.model_validate_json(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    assert saved.round_spent == 0 and saved.round_elapsed == {} and saved.minutes == before
    assert saved.round_plan["used"] == [] and saved.round_plan["done"] == []
    restored = Hub(EXAMPLE, state=saved, cast=PartyCast(fail=True), rules=Rules(dice=fake))
    assert saved.round_plan["input"] == [list(pair) for pair in hub.pool]  # arrival order remains intact
    expected = list(reversed(hub.pool))
    assert restored.game.pending_party() == expected  # only the chosen uncompleted execution order is resumed
    assert await restored.game.arrange_party(hub.pool) == expected
    assert not restored.cast.batches  # cancellation did not require another model arrangement
    await restored._settle()
    assert [who for who, _ in restored.cast.ruled] == ["lin", "zhouheng"]
    assert restored.state.minutes == before + 30 and not restored.game.pending_party()
    assert not restored.cast.batches and not restored.cast.individual_plans
    await restored.game.close()
    await hub.game.close()


async def test_a_reopened_room_resumes_the_old_window_before_collecting_new_input(fake, monkeypatch):
    original = make_hub(fake)
    original.sits = {"zhouheng": "a", "lin": "b"}
    original.pool = [("zhouheng", "我试着开锁"), ("lin", "我拖住布道者")]
    before = original.state.minutes
    run = original._run
    calls = 0

    async def stop_between_actions(text, inv_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError
        await run(text, inv_id, **kwargs)

    monkeypatch.setattr(original, "_run", stop_between_actions)
    with pytest.raises(asyncio.CancelledError):
        await original._settle()
    await original.game.close()
    original._save()
    saved = GameState.model_validate_json(paths.save(original.state.slot).read_text(encoding="utf-8"))
    assert not saved.pending_turn and saved.round_elapsed == {"lin": 10} and saved.round_plan["done"] == ["1"]

    class Resuming(PartyCast):
        def __init__(self):
            super().__init__(fail=True)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def arbiter(self, state, action, at=None, *, coordination="", **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().arbiter(state, action, at, coordination=coordination, **kwargs)

    cast = Resuming()
    hub = Hub(EXAMPLE, state=saved, cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = [await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws") for _ in range(2)]
            try:
                for ws, name, seat in ((a, "甲", "zhouheng"), (b, "乙", "lin")):
                    await ws.send_json({"t": "hello", "name": name})
                    await until(ws, lambda x: x["t"] == "welcome")
                    await ws.send_json({"t": "sit", "investigator": seat})
                await asyncio.wait_for(cast.entered.wait(), 5)
                # A sealed saved batch resumes first; new input is rejected without silently queuing it.
                await a.send_json({"t": "act", "text": "我查看门外"})
                rejected = await until(a, lambda x: x["t"] == "action_rejected")
                assert rejected["text"] == "我查看门外" and not hub.early
                cast.release.set()
                next_window = await until(a, pool_open)
                assert next_window["count"] == 0 and next_window["mine"] == {"now": [], "next": []}
                assert hub.state.minutes - before == 30  # not the completed ten minutes plus a new thirty
                assert [who for who, _ in cast.ruled] == ["zhouheng"]
                assert not cast.batches and not cast.individual_plans
                played = [e.text for e in hub.state.log if e.kind == "player"]
                assert played.count("我拖住布道者") == 1 and played.count("我试着开锁") == 1
                assert "我查看门外" not in played
            finally:
                cast.release.set()
                await asyncio.gather(a.close(), b.close())
    finally:
        await closed(task)
