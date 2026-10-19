"""MCP requests retain settled results while an interrupted stage is retried."""

import pytest

from autokeeper import mcp_server as server
from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make


@pytest.fixture
async def game(fake, monkeypatch):
    module = Module.load(paths.EXAMPLE)
    game, cast, events = make(module, fake, {}, location="tower")
    game.state.slot = "mcp-recovery"
    monkeypatch.setattr(server.S, "game", game)
    monkeypatch.setattr(server.S, "module", module)
    game.checkpoint, game.turn_checkpoint = server._save, server._keep_turn
    yield game, cast, events
    await server.S.game.close()


async def test_mcp_roll_resumes_after_a_broken_notification_and_json_reload(game, fake):
    original, _, _ = game
    before = original.state.minutes
    fake.queue(5, 20)

    async def broken(event):
        if event["type"] == "dice":
            raise ConnectionError("connection lost")

    original.emit = broken
    with pytest.raises(ConnectionError):
        await server.skill_check("侦查", minutes=15)
    assert len(original.state.checks) == 1 and original.state.pending_mcp
    loaded = GameState.model_validate_json(paths.save(original.state.slot).read_text(encoding="utf-8"))
    await original.close()
    resumed = Game(Module.load(paths.EXAMPLE), loaded, FakeCast({}), Rules(dice=fake))
    server.S.game, server.S.module = resumed, resumed.m
    resumed.checkpoint, resumed.turn_checkpoint = server._save, server._keep_turn
    result = await server.skill_check("侦查", minutes=15)
    assert len(result["checks"]) == len(loaded.checks) == 1
    assert loaded.minutes == before + 15 and loaded.turn == 1
    assert list(fake.values) == [20] and not loaded.pending_mcp and not loaded.pending_resolution


async def test_mcp_dialogue_failure_keeps_the_original_success_and_unlocked_gate(game, fake):
    current, cast, _ = game
    before = current.state.minutes
    fake.queue(5, 20)
    speak = cast.npc

    async def broken(*args, **kwargs):
        raise RuntimeError("model unavailable")

    cast.npc = broken
    with pytest.raises(RuntimeError):
        await server.npc_say("warden", "请让我看一下日记。", check_skill="说服")
    assert "c_diary" in current.state.clues and current.state.pending_mcp["turn"]["checks"]
    cast.npc = speak
    result = await server.npc_say("warden", "请让我看一下日记。", check_skill="说服")
    assert len(result["checks"]) == len(current.state.checks) == 1 and result["npc_lines"]
    assert current.state.minutes == before + 10 and list(fake.values) == [20]
    assert not current.state.pending_mcp and current.state.turn == 1


async def test_a_new_mcp_action_cannot_replace_an_interrupted_request(game, fake):
    current, cast, _ = game
    fake.queue(5)

    async def broken(*args, **kwargs):
        raise RuntimeError("model unavailable")

    cast.npc = broken
    with pytest.raises(RuntimeError):
        await server.npc_say("warden", "请帮忙。", check_skill="说服")
    before = current.state.model_dump(mode="json")
    with pytest.raises(ValueError, match="原参数"):
        await server.move("square")
    assert current.state.model_dump(mode="json") == before
    hub = object.__new__(Hub)
    hub.state = current.state
    snapshot = hub._view()
    assert "pending_mcp" not in snapshot and "pending_resolution" not in snapshot


async def test_successful_identical_mcp_requests_are_separate_turns(game, fake):
    current, _, _ = game
    before = current.state.minutes
    fake.queue(5, 20)
    await server.skill_check("侦查", minutes=1)
    await server.skill_check("侦查", minutes=1)
    assert len(current.state.checks) == 2 and current.state.turn == 2
    assert current.state.minutes == before + 2 and not current.state.pending_mcp
