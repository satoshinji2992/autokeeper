"""World planning has its own route; NPC dialogue keeps its own model and knowledge scope."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.harness import Reply, trace
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.tui.modals.settings import SettingsModal
from autokeeper.world.agents import Ask, CastBrain


@pytest.fixture(autouse=True)
def clean_model_environment(monkeypatch):
    for suffix in ("PRESET", "MODEL", "FALLBACK", "MODE", "IMPROV", "SEARCH", "BUDGET_USD"):
        monkeypatch.delenv(f"AUTOKEEPER_{suffix}", raising=False)


def test_world_follows_the_effective_arbiter_thinking_low_until_explicitly_configured():
    arbiter = Role("langchain", "deepseek:deepseek-flash", "high", base_url="http://127.0.0.1:8000/v1")
    cfg = config.Config(roles={"arbiter": arbiter, "npc": config.HAIKU})
    assert cfg.role("world") == replace(arbiter, effort="low") and "world" not in cfg.roles
    assert "world" in cfg.role_names
    cfg.roles["arbiter"] = replace(arbiter, model="deepseek:deepseek-v4-pro", effort="max")
    assert cfg.role("world") == replace(arbiter, model="deepseek:deepseek-v4-pro", effort="low")  # the route, not its thinking
    cfg.roles["world"] = config.SONNET
    assert cfg.role("world") == config.SONNET and cfg.role("npc") == config.HAIKU


@pytest.mark.parametrize("preset", config.PRESETS)
def test_real_presets_load_world_without_changing_existing_routes(preset, monkeypatch):
    before = {name: dict(roles) for name, roles in config.PRESETS.items()}
    monkeypatch.setenv("AUTOKEEPER_PRESET", preset)
    cfg = config.load()
    assert cfg.roles == config.PRESETS[preset]
    assert cfg.role("world") == replace(cfg.role("arbiter"), effort="low")
    assert config.PRESETS == before


def test_world_override_inherits_the_final_arbiter_settings_independent_of_toml_order(tmp_path):
    route = tmp_path / "routes.toml"
    route.write_text('''preset = "deepseek"
[roles.world]
effort = "max"
[roles.arbiter]
model = "deepseek:deepseek-v4-pro"
window = 7500
[roles.all]
base_url = "http://127.0.0.1:8123/v1"
fallback = "ollama:qwen3:14b"
''', encoding="utf-8")
    cfg = config.load(route)
    assert cfg.role("world") == replace(cfg.role("arbiter"), effort="max")
    assert cfg.role("npc").model == "deepseek:deepseek-flash" and cfg.role("npc").effort == "low"
    assert "world" in cfg.roles


def test_world_can_select_a_model_independently_and_settings_identifies_it(tmp_path):
    route = paths.USER_DIR / "config.toml"
    route.parent.mkdir(parents=True)
    route.write_text('''preset = "deepseek"
[roles.world]
backend = "codex"
model = "default"
effort = "high"
''', encoding="utf-8")
    cfg = config.load()
    assert cfg.role("world") == Role("codex", "default", "high")
    assert cfg.role("arbiter") == config.PRESETS["deepseek"]["arbiter"]
    assert cfg.role("npc") == config.PRESETS["deepseek"]["npc"]
    assert "世界推演：default" in SettingsModal()._model_summary().plain
    assert "单独配置" in SettingsModal()._model_summary().plain


async def test_world_sessions_use_world_trace_but_mind_usage_while_dialogue_uses_npc(monkeypatch):
    class Scripted(hm.Model):
        mode = "json"

        async def _call(self, messages, *, schema, images, on_delta):
            data = {"decisions": []}
            return Reply(text=json.dumps(data), data=data, usage={"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setitem(hm.MODELS, "world_test", Scripted)
    roles = {name: Role("world_test", f"scripted-{name}", None) for name in config.DEFAULT_ROLES}
    world = Role("world_test", "scripted-world-planner", "high")
    cfg = config.Config(roles=roles | {"world": world})
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(cfg, module)
    try:
        ask = Ask(npcs=["warden"], views={"warden": "守钟人自己的资料"}, known={"warden": []}, reasons={}, t=state.minutes)
        result = await CastBrain(cast, state).decide(ask)
        mind = cast.sessions["mind:warden"]
        dialogue = cast._npc(state, "warden")
        assert result is not None and mind.spec == world and mind.role == "world" and mind.memory == 0
        assert dialogue.spec == cfg.role("npc") and dialogue.role == "npc" and dialogue is not mind
        assert state.usage["mind"]["calls"] == 1 and "world" not in state.usage and "npc" not in state.usage
        assert cast.debug_calls[-1]["agent"] == "world" and cast.debug_calls[-1]["session"] == "mind:warden"
        assert any(row.get("agent") == "world" for row in trace.read())
    finally:
        await cast.close()
