"""Textual's timers on Windows sleep in a thread of the default pool each; a game screen's dozens of
timers took the pool, and pictures and the opening's preparation waited minutes behind them (the
user, 2026-10-22). On Windows they sleep on the event loop instead (tui/app.plain_timer_sleep)."""

from __future__ import annotations

import asyncio

import textual.timer
from textual.app import App

from autokeeper.tui.app import plain_timer_sleep


async def test_a_timers_sleep_takes_no_thread_and_its_ticks_still_come(monkeypatch):
    monkeypatch.setattr(textual.timer, "sleep", textual.timer.sleep)  # put back afterwards
    plain_timer_sleep()
    loop = asyncio.get_running_loop()
    threads: list[object] = []
    real = loop.run_in_executor
    monkeypatch.setattr(loop, "run_in_executor", lambda *a, **k: threads.append(a) or real(*a, **k))
    await textual.timer.sleep(0.01)
    assert threads == []  # on the loop, not in the pool

    ticks: list[int] = []

    class Ticking(App):
        def on_mount(self) -> None:
            self.set_interval(0.01, lambda: ticks.append(1))

    async with Ticking().run_test() as pilot:
        for _ in range(50):
            if len(ticks) >= 3:
                break
            await pilot.pause(0.02)
    assert len(ticks) >= 3
