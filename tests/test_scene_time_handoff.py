"""Actual sockets: a quiet scene's KP may cut blank time, never another player's choice."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import aiohttp
import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Ruling, TimeHandoff
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import SetFlag

from .test_net import TableCast, opened, pool_open, until
from .test_net_scenes import joined
from .test_request_execution import module
from .test_scene import WRITER, sandbox  # noqa: F401
from .test_scene_runtime import made


async def eventually(check):
    async with asyncio.timeout(5):
        while not check():
            await asyncio.sleep(0.005)


def make_hub(fake, factory):
    m = module()
    m.location("tower").within = None
    a = m.pregens[0].model_copy(deep=True)
    b = a.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [a, b], human=[a.id, b.id], slot="clock-handoff")
    state.location, state.minutes, state.apart = "tower", 600, {"lin": "square"}
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake),
              scene_cast_factory=factory, scene_rules_factory=lambda _: Rules(dice=fake))
    hub.m.events.clear()
    hub.m.endings.clear()
    hub.m.foreshadow.clear()
    hub.m.agendas.clear()
    hub.m.location("tower").within = None
    hub.window = 0.01
    return hub


class ClockCast(TableCast):
    async def intent(self, state, text):
        return Action(kind="use", summary=text)

    async def arbiter(self, state, action, at=None, **kwargs):
        return Ruling(needs_roll="掷骰" in action.summary, skill="侦查",
                      minutes=20 if "长行动" in action.summary else 0)


@pytest.mark.parametrize("with_note", [False, True])
async def test_quiet_scene_kp_allows_bounded_cut_without_inventing_an_action(fake, with_note):
    entered, allow = asyncio.Event(), asyncio.Event()
    calls = []

    class Agree(ClockCast):
        async def scene_time(self, state, end):
            calls.append((state.investigator.id, state.minutes, end, list(state.log)))
            entered.set()
            await allow.wait()
            return TimeHandoff(advance=True, reason="本场暂无紧迫选择，可以略过空白。")

    hub = make_hub(fake, lambda _: Agree())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            note = {"original": "有空去书房", "remaining": "之后再去书房", "reason": "暂留意图，不是正在执行"}
            if with_note:
                hub.state.unfinished["lin"] = note
            await a.send_json({"t": "act", "text": "办理长行动"})
            await asyncio.wait_for(entered.wait(), 5)
            assert calls[0][:3] == ("lin", 600, 620)
            assert not calls[0][3]  # no other scene's raw action or dialogue is forwarded
            assert hub._context("lin").state.minutes == 600
            assert hub._context("zhouheng").state.minutes == 600
            allow.set()
            await until(a, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            clock = await until(b, lambda msg: msg.get("ev", {}).get("type") == "time")
            assert clock["ev"]["clock"] == "D1 10:20"
            assert hub._context("lin").state.minutes == 620
            assert not any(entry.kind == "player" and entry.who == "林秋" for entry in hub.state.log)
            if with_note:
                assert hub.state.unfinished["lin"] == note
            assert len(calls) == 1
    finally:
        allow.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_declined_cut_keeps_input_open_and_is_not_polled_repeatedly(fake):
    calls = []

    class Refuse(ClockCast):
        async def scene_time(self, state, end):
            calls.append(end)
            return TimeHandoff(advance=False, reason="调查员还有一个必须先决定的问题。")

    hub = make_hub(fake, lambda _: Refuse())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await a.send_json({"t": "act", "text": "办理长行动"})
            await eventually(lambda: len(calls) == 1 and hub._context("lin").time_task.done())
            for _ in range(5):
                hub._scene_tables.runtime._notify()
                await asyncio.sleep(0)
            assert calls == [620]
            assert hub._context("lin").open and hub._context("lin").state.minutes == 600
            await b.send_json({"t": "done"})
            await until(a, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            assert hub._context("lin").state.minutes == 620
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_arrival_merges_the_scenes_and_updates_the_waiting_players_clock(fake):
    class Reunion(ClockCast):
        async def intent(self, state, text):
            return Action(kind="move", summary=text, destination="square")

        async def scene_time(self, state, end):
            return TimeHandoff(advance=True, reason="同伴返程期间可以略过空白。")

    hub = make_hub(fake, lambda _: Reunion())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            elapsed = hub._context("zhouheng").game._travel("tower", "square")
            await a.send_json({"t": "act", "text": "我去广场会合"})
            await until(a, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            await until(b, lambda msg: msg.get("state", {}).get("minutes") == 600 + elapsed)
            await eventually(lambda: hub._context("lin").ident == hub._context("zhouheng").ident)
            assert hub._view("lin")["minutes"] == 600 + elapsed
            assert hub._context("lin").scene.members == ["zhouheng", "lin"]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_an_actual_unanswered_roll_cannot_be_fast_forwarded(fake):
    fake.values.append(30)
    calls = []

    class Agree(ClockCast):
        async def scene_time(self, state, end):
            calls.append(end)
            return TimeHandoff(advance=True, reason="现在可以接续。")

    hub = make_hub(fake, lambda _: Agree())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await b.send_json({"t": "act", "text": "我要掷骰侦查"})
            offer = (await until(b, lambda msg: msg["t"] == "offer"))["offer"]
            await a.send_json({"t": "act", "text": "办理长行动"})
            await until(a, lambda msg: "等待其他现场" in msg.get("ev", {}).get("text", ""))
            assert not calls and hub._context("lin").state.minutes == 600
            assert list(fake.values) == [30]
            await b.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
            await until(b, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            await until(a, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            assert not fake.values and calls == [620]
            assert offer["respond_id"] in hub._context("lin").settled
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_new_declaration_invalidates_even_a_cancellation_resistant_clock_reply(fake):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = []
    fake.values.append(30)

    class Late(ClockCast):
        async def scene_time(self, state, end):
            calls.append(end)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                return TimeHandoff(advance=True, reason="这是已经过时的同意。")

    hub = make_hub(fake, lambda _: Late())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await a.send_json({"t": "act", "text": "办理长行动"})
            await asyncio.wait_for(entered.wait(), 5)
            await b.send_json({"t": "act", "text": "我要掷骰侦查"})
            await until(b, lambda msg: msg["t"] == "offer")
            assert cancelled.is_set()
            assert hub._context("lin").state.minutes == 600
            assert hub._context("zhouheng").state.minutes == 600
            assert hub._context("lin").scene.passed_until is None
            assert list(fake.values) == [30] and calls == [620]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_shutdown_cancels_a_clock_proposal_without_committing_its_late_agreement(fake):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Late(ClockCast):
        async def scene_time(self, state, end):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                return TimeHandoff(advance=True, reason="停服时已经过时的建议。")

    hub = make_hub(fake, lambda _: Late())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await a.send_json({"t": "act", "text": "办理长行动"})
            await asyncio.wait_for(entered.wait(), 5)
            worker = hub._context("lin")
            proposal = worker.time_task
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            assert cancelled.is_set() and proposal.done()
            assert worker.scene.at == 600 and worker.scene.passed_until is None
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_handoff_request_uses_the_earliest_live_blocked_action_and_is_bounded(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    try:
        runtime.scenes[a].pending_turn = {"input": "原声明"}
        runtime.reserve(a, Action(kind="use", summary="长行动"), Ruling(needs_roll=False, minutes=20))
        runtime.scenes[a].reservation["blocked"] = runtime.blockers(a)
        assert runtime.time_requested(b) == 620
        assert runtime.pass_time(b, 620)
        assert runtime.time_requested(b) is None
        runtime.commit_time(a, 620)
        assert runtime.scenes[b].at == 620
        assert runtime.scenes[b].notices == [{"type": "time_cut", "start": 600, "end": 620}]
        runtime.revoke_pass(b)
        runtime.scenes[a].reservation.clear()
        assert runtime.time_requested(b) is None
    finally:
        await runtime.close()


async def test_an_already_started_action_can_request_more_time_for_its_real_cost(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    waiting = None
    try:
        game = runtime.game(a)
        runtime.scenes[a].pending_turn = {"input": "已开始的行动"}
        runtime.reserve(a, Action(kind="use", summary="原动作"), Ruling(needs_roll=False, minutes=10))
        assert runtime.pass_time(b, 610)
        await runtime.wait(a)
        turn = Turn()
        game._spend(10, turn)
        assert runtime.scenes[b].at == 610
        runtime.revoke_pass(b)
        waiting = asyncio.create_task(runtime.wait(a, 620))
        await eventually(lambda: runtime.scenes[a].reservation.get("blocked"))
        assert runtime.scenes[a].reservation["phase"] == "running"
        assert runtime.time_requested(b) == 620
        assert runtime.pass_time(b, 620)
        await asyncio.wait_for(waiting, 3)
        game._spend(10, turn)
        assert turn.interval["start"] == 600 and turn.interval["end"] == 620
        assert runtime.scenes[b].at == runtime.scenes[a].at == 620
    finally:
        if waiting is not None:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await runtime.close()


async def test_a_saved_bounded_permission_resumes_the_original_time_only_once(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    restored = None
    try:
        runtime.scenes[a].pending_turn = {"input": "原来的长行动"}
        runtime.reserve(a, Action(kind="use", summary="原动作"), Ruling(needs_roll=False, minutes=20))
        runtime.scenes[a].reservation["blocked"] = runtime.blockers(a)
        runtime.pass_time(b, 620)
        saved = GameState.model_validate_json(runtime.state.model_dump_json())
        restored, _, _ = made(fake, state=saved)
        assert restored.scenes[b].at == 600
        assert restored.scenes[b].passed_until == 620
        await restored.wait(a)
        assert restored.scenes[a].pending_turn["input"] == "原来的长行动"
        assert restored.scenes[a].reservation["end"] == 620
        restored.game(a)._spend(20, Turn())
        restored.commit_time(a, 620)
        assert restored.scenes[b].at == restored.scenes[a].at == 620
        assert restored.scenes[b].notices == [{"type": "time_cut", "start": 600, "end": 620}]
        assert not any(entry.kind == "player" for entry in restored.state.log)
    finally:
        if restored is not None:
            await restored.close()
        await runtime.close()


async def test_a_world_revision_requires_a_fresh_kp_decision_before_the_cut(fake):
    gates = [asyncio.Event(), asyncio.Event()]
    calls = []

    class Recheck(ClockCast):
        async def scene_time(self, state, end):
            index = len(calls)
            calls.append(state.record.seq)
            await gates[min(index, 1)].wait()
            return TimeHandoff(advance=True, reason="按调用时的实际局势判断。")

    hub = make_hub(fake, lambda _: Recheck())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await a.send_json({"t": "act", "text": "办理长行动"})
            await eventually(lambda: len(calls) == 1)
            assert hub.game.world.apply(SetFlag(flag="bell_stopped")).ok
            gates[0].set()
            await eventually(lambda: len(calls) == 2)
            assert calls[1] > calls[0]
            assert hub._context("lin").state.minutes == 600
            assert hub._context("zhouheng").state.minutes == 600
            gates[1].set()
            await until(a, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            assert hub._context("lin").state.minutes == 620
    finally:
        for gate in gates:
            gate.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_clock_consultation_uses_full_keeper_preparation_without_sharing_its_session(fake, monkeypatch):
    m = module()
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.unfinished[st.investigator.id] = {"original": "有空回来取信", "remaining": "之后取信", "reason": "不急"}
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES}), m)
    seen = []

    async def send(key, session, state, prompt, **kwargs):
        seen.append((key, session, prompt, kwargs))
        return SimpleNamespace(data={"advance": True, "reason": "当前可以略过空白。"})

    monkeypatch.setattr(cast, "_send", send)
    try:
        original = cast._keeper(st)
        original.transcript.append({"role": "user", "content": "本场原会话"})
        assert (await cast.scene_time(st, st.minutes + 20)).advance
        key, session, prompt, kwargs = seen[0]
        assert key == "scene_time" and session is not original
        assert session.system == original.system and kwargs["bind"] is False
        assert session.transcript == original.transcript and session.transcript is not original.transcript
        assert "不能替调查员" in prompt and "新玩家声明会撤销许可" in prompt
        assert "有空回来取信" in prompt
        assert original.transcript == [{"role": "user", "content": "本场原会话"}]
    finally:
        await cast.close()
