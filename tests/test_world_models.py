"""Runtime roles keep their effective routes without a separate NPC planning model."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from autokeeper import config, paths
from autokeeper.config import Role
from autokeeper.tui.modals.settings import SettingsModal


@pytest.fixture(autouse=True)
def clean_model_environment(monkeypatch):
    for suffix in ("PRESET", "MODEL", "FALLBACK", "MODE", "IMPROV", "SEARCH", "BUDGET_USD"):
        monkeypatch.delenv(f"AUTOKEEPER_{suffix}", raising=False)


def test_the_keepers_plan_takes_the_keepers_model_thinking_low():
    keeper = Role("langchain", "deepseek:deepseek-flash", "high")
    cfg = config.Config(roles={"keeper": keeper})
    assert cfg.role("plan") == replace(keeper, effort="low") and "plan" in cfg.role_names
    cfg.roles["plan"] = config.HAIKU
    assert cfg.role("plan") == config.HAIKU


@pytest.mark.parametrize("preset", config.PRESETS)
def test_real_presets_leave_derived_plan_routes_implicit(preset, monkeypatch):
    before = {name: dict(roles) for name, roles in config.PRESETS.items()}
    monkeypatch.setenv("AUTOKEEPER_PRESET", preset)
    cfg = config.load()
    assert cfg.roles == config.PRESETS[preset]
    assert cfg.role("plan") == replace(cfg.role("keeper"), effort="low")
    assert "world" not in cfg.role_names and "world" not in config.ROLES_SHOWN
    assert "minds" not in {field.name for field in fields(config.Config)}
    assert config.PRESETS == before


def test_plan_override_inherits_final_keeper_settings_independent_of_toml_order(tmp_path):
    route = tmp_path / "routes.toml"
    route.write_text('''preset = "deepseek"
[roles.plan]
effort = "max"
[roles.keeper]
model = "deepseek:deepseek-v4-pro"
window = 7500
[roles.all]
base_url = "http://127.0.0.1:8123/v1"
fallback = "ollama:qwen3:14b"
''', encoding="utf-8")
    cfg = config.load(route)
    assert cfg.role("plan") == replace(cfg.role("keeper"), effort="max")
    assert cfg.role("npc").model == "deepseek:deepseek-flash" and cfg.role("npc").effort == "low"
    assert "plan" in cfg.roles and "world" not in cfg.role_names


def test_settings_describes_active_roles_without_a_separate_world_route():
    route = paths.USER_DIR / "config.toml"
    route.parent.mkdir(parents=True)
    route.write_text('''preset = "deepseek"
[roles.keeper]
backend = "codex"
model = "default"
effort = "high"
''', encoding="utf-8")
    cfg = config.load()
    assert cfg.role("keeper") == Role("codex", "default", "high")
    assert cfg.role("plan") == Role("codex", "default", "low")
    assert cfg.role("arbiter") == config.PRESETS["deepseek"]["arbiter"]
    assert cfg.role("npc") == config.PRESETS["deepseek"]["npc"]
    summary = SettingsModal()._model_summary().plain
    assert "deepseek · 标准 · default" in summary and "世界推演" not in summary


@pytest.mark.parametrize(("own_backend", "shared_backend", "expected"), [
    (None, None, "langchain"),
    ("offline_custom", None, "offline_custom"),
    (None, "offline_shared", "offline_shared"),
    ("offline_custom", "offline_shared", "offline_custom"),
])
def test_custom_roles_infer_a_named_route_but_preserve_explicit_backends(tmp_path, own_backend, shared_backend, expected):
    configured = 'preset = "deepseek"\n'
    if shared_backend:
        configured += f'[roles.all]\nbackend = "{shared_backend}"\n'
    configured += '[roles.tactics]\nmodel = "deepseek:deepseek-flash"\n'
    if own_backend:
        configured += f'backend = "{own_backend}"\n'
    source = tmp_path / "custom-route.toml"
    source.write_text(configured, encoding="utf-8")
    cfg = config.load(source)
    assert cfg.role("tactics").model == "deepseek:deepseek-flash"
    assert cfg.role("tactics").backend == expected
    assert "tactics" in cfg.role_names
    if shared_backend is None:
        assert all(cfg.role(name) == role for name, role in config.PRESETS["deepseek"].items())
