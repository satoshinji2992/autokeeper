"""Independent hosts must not each plan the same world's elapsed interval."""

from __future__ import annotations

import asyncio

from autokeeper.engine.game import Turn
from autokeeper.world import scheduler

from .test_scene_runtime import made


async def test_two_scene_hosts_advance_and_review_the_shared_world_once(fake, monkeypatch):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    runtime.state.record.simulated = 600
    runtime.state_view(a).minutes = runtime.state_view(b).minutes = 720
    entered, release = asyncio.Event(), asyncio.Event()
    intervals, reviews = [], []

    async def advance(world, brain, start, end, **kwargs):
        intervals.append((start, end))
        entered.set()
        await release.wait()
        return scheduler.Report(kwargs["tick"])

    async def review(state, material):
        reviews.append(state.minutes)
        await asyncio.sleep(0)
        return None

    monkeypatch.setattr(scheduler, "advance", advance)
    monkeypatch.setattr(scheduler, "reactions_ready", lambda *args, **kwargs: False)
    ga.cast.review = gb.cast.review = review
    tasks = []
    try:
        tasks.append(asyncio.create_task(ga._live(Turn())))
        await entered.wait()
        tasks.append(asyncio.create_task(gb._live(Turn())))
        await asyncio.sleep(0)
        assert intervals == [(600, 720)]
        release.set()
        await asyncio.gather(*tasks)
        assert intervals == [(600, 720)]
        assert runtime.state.record.simulated == 720
        await asyncio.gather(ga._look(), gb._look())
        assert reviews == [720]
        assert runtime.state.record.reviewed_at == 720
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()
