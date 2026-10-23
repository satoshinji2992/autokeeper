"""A real move retires its scene and carries unstarted original input through restart."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import aiohttp

from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, opened, pool_open, until
from .test_net_scenes import joined

MOVE = "我独自前往报社，林秋留在广场。"
QUEUED = "我留在广场，抄下招牌上能看清的文字。"
OTHER = "我在报社整理刚才看到的门牌。"


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


class SplitCast(TableCast):
    def __init__(self, ident, *, started=None, release=None):
        super().__init__()
        self.ident, self.started, self.release = ident, started, release
        self.inputs = []
        self.closed = False
        self.background = asyncio.create_task(asyncio.Event().wait(), name=f"cast-background:{ident}")

    async def intent(self, state, text):
        receipt = state.round_plan
        step = next((row for row in receipt.get("steps", [])
                     if receipt["input"][int(row["token"])] == [state.investigator.id, text]), {})
        self.inputs.append({"who": state.investigator.id, "text": text, "at": state.minutes,
                            "received": step.get("received"), "place": state.location})
        if text == MOVE:
            if self.started is not None:
                self.started.set()
                await self.release.wait()
            return Action(kind="move", summary=text, destination="office", alone=True)
        return Action(kind="daily", summary=text)

    async def arbiter(self, state, action, at=None, **kwargs):
        return Ruling(needs_roll=False, minutes=1)

    async def keeper(self, state, report, on_delta=None, lore=""):
        return f"{state.investigator.id}|{state.location}|现场继续。"

    async def close(self):
        self.closed = True
        self.background.cancel()
        await asyncio.gather(self.background, return_exceptions=True)


def host(state, fake, casts, *, started=None, release=None):
    def factory(ident):
        cast = SplitCast(ident, started=started, release=release)
        casts[ident] = cast
        return cast  # Every live scene has its own sessions, including after a split.

    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake),
              scene_cast_factory=factory, scene_rules_factory=lambda _ident: Rules(dice=fake))
    hub.m.events, hub.m.endings, hub.m.foreshadow, hub.m.showcases, hub.m.agendas = [], [], [], [], []
    hub.m.location("square").travel_minutes["office"] = 10
    hub.window = 0.01
    return hub


async def stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_actual_move_preserves_queued_original_time_retires_background_and_restores_both_scenes(fake):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, friend], human=[lead.id, friend.id], slot="socket-scene-split")
    state.minutes = 600
    parsing, release = asyncio.Event(), asyncio.Event()
    casts = {}
    hub = host(state, fake, casts, started=parsing, release=release)
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            x, y = await joined(http, hub, lead.id), await joined(http, hub, friend.id)
            await until(x, pool_open)
            await until(y, pool_open)
            source = hub._context(lead.id)
            assert source is hub._context(friend.id) and source is not hub
            old_cast = source.cast
            assert old_cast is casts[source.ident]
            background = source.game._recounting = asyncio.create_task(asyncio.Event().wait())
            await x.send_json({"t": "act", "text": MOVE})
            await asyncio.wait_for(parsing.wait(), 5)
            await y.send_json({"t": "act", "text": QUEUED})
            await eventually(lambda: (friend.id, QUEUED) in source.early)
            original_receipt = next(dict(row) for row in source.state.queued_receipts
                                    if row["seat"] == friend.id and row["text"] == QUEUED)
            assert original_receipt["at"] == 600
            await y.close()
            await eventually(lambda: hub.sits.get(friend.id) is None)
            release.set()
            await eventually(lambda: source.halted and source.cast.closed)
            a, b = hub._context(lead.id), hub._context(friend.id)
            assert a is not b and a is not source and b is not source
            assert hub.state.positions == {lead.id: "office", friend.id: "square"}
            assert a.scene.at == 610 and b.scene.at == 600 and source.scene.closed
            assert (friend.id, QUEUED) in b.early
            assert original_receipt in b.state.queued_receipts
            assert not any(row["text"] == QUEUED for cast in casts.values() for row in cast.inputs)
            assert background.cancelled() and source.game._recounting is None
            assert old_cast.background.cancelled()
            assert source.ident not in hub._scene_tables.runtime._games
            assert source.ident not in hub._scene_tables.runtime._graphs
            assert len(casts) == 3 and len({id(cast) for cast in casts.values()}) == 3
            hub._save()
            loaded = GameState.model_validate_json(paths.save(state.slot).read_text(encoding="utf-8"))
            saved_b = next(scene for scene in loaded.scenes.values() if not scene.closed and friend.id in scene.members)
            assert saved_b.queued_actions == [(friend.id, QUEUED)] and original_receipt in saved_b.queued_receipts
    finally:
        release.set()
        await stop(task)
    assert all(cast.closed and cast.background.cancelled() for cast in casts.values())

    resumed_casts = {}
    resumed = host(loaded, fake, resumed_casts)
    task = await opened(resumed)
    try:
        async with aiohttp.ClientSession() as http:
            y = await joined(http, resumed, friend.id)
            x = await joined(http, resumed, lead.id)
            await until(x, pool_open)
            await x.send_json({"t": "act", "text": OTHER})
            done_y = await until(y, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            b = resumed._context(friend.id)
            await eventually(lambda: b.open and not b.scene.pending_turn)
            assert b.scene.at == 601  # A's ten-minute walk did not consume B's opportunity to act.
            await y.send_json({"t": "done"})  # B explicitly allows time to pass before A's later result.
            done_x = await until(x, lambda msg: msg.get("ev", {}).get("type") == "keeper_end")
            assert done_x["ev"]["text"] == "zhouheng|office|现场继续。"
            assert done_y["ev"]["text"] == "lin|square|现场继续。"
            a, b = resumed._context(lead.id), resumed._context(friend.id)
            assert a is not b and a.cast is not b.cast and len(resumed_casts) == 2
            # keeper_end is the delivered narration, before the turn journal is closed.
            await eventually(lambda: not a.state.pending_turn and not b.state.pending_turn)
            assert a.scene.at == b.scene.at == 611
            original_calls = [row for cast in resumed_casts.values() for row in cast.inputs if row["text"] == QUEUED]
            assert original_calls == [{"who": friend.id, "text": QUEUED, "at": 600, "received": 600, "place": "square"}]
            assert not any(row["text"] == MOVE for cast in resumed_casts.values() for row in cast.inputs)
            assert not a.state.pending_turn and not b.state.pending_turn
            assert not b.state.queued_actions and not b.state.queued_receipts
            assert not fake.values and not fake.tables
    finally:
        await stop(task)
    assert all(cast.closed and cast.background.cancelled() for cast in resumed_casts.values())
