"""One command picks the model and keeps its key where every later start finds it (cli.login)."""

from __future__ import annotations

import os
import stat

import pytest
from typer.testing import CliRunner

from autokeeper import config, paths
from autokeeper.cli import app


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "AUTOKEEPER_MODEL", "AUTOKEEPER_PRESET", "AUTOKEEPER_MODE"):
        monkeypatch.setenv(name, "")  # so the key a test saves is taken away after it
        monkeypatch.delenv(name)
    monkeypatch.setattr("shutil.which", lambda name: None)  # no local claude or codex


def test_a_key_replaces_its_old_line_and_only_its_owner_can_read_it():
    (paths.USER_DIR).mkdir(parents=True, exist_ok=True)
    (paths.USER_DIR / ".env").write_text("OTHER=1\nDEEPSEEK_API_KEY=old\n", encoding="utf-8")
    target = config.save_key("DEEPSEEK_API_KEY", "sk-new")
    assert target.read_text(encoding="utf-8") == "OTHER=1\nDEEPSEEK_API_KEY=sk-new\n"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-new"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_choosing_deepseek_asks_for_its_key_and_remembers_the_choice():
    out = CliRunner().invoke(app, ["login"], input="1\nsk-test\n")
    assert out.exit_code == 0, out.output
    assert "sk-test" not in out.output  # typed hidden, never echoed
    assert "DEEPSEEK_API_KEY=sk-test" in (paths.USER_DIR / ".env").read_text(encoding="utf-8")
    assert config.ui_settings()["preset"] == "deepseek"


def test_a_script_can_log_in_without_questions():
    out = CliRunner().invoke(app, ["login", "deepseek", "--key", "sk-script"])
    assert out.exit_code == 0, out.output
    assert config.load().preset == "deepseek" and os.environ["DEEPSEEK_API_KEY"] == "sk-script"


def test_skipping_changes_nothing():
    out = CliRunner().invoke(app, ["login"], input="4\n")
    assert out.exit_code == 0 and "跳过" in out.output
    assert not (paths.USER_DIR / ".env").exists() and "preset" not in config.ui_settings()


def test_an_unknown_choice_is_refused():
    out = CliRunner().invoke(app, ["login", "gemini"])
    assert out.exit_code == 1 and "没有这个选项" in out.output


def flat(out) -> str:
    return out.output.replace("\n", "")


def routes() -> set[tuple[str, str]]:
    cfg = config.load()
    return {(cfg.role(name).backend, cfg.role(name).model.split(":")[0]) for name in config.DEFAULT_ROLES}


def test_switching_to_deepseek_drops_the_one_model_picked_in_the_settings():
    config.save_ui_settings({"preset": "claude", "model": "claude-opus-5-5"})  # every role on Claude, from the game's 模型 dialog
    out = CliRunner().invoke(app, ["login", "deepseek", "--key", "sk-x"])
    assert out.exit_code == 0 and "以后默认用 deepseek" in flat(out)
    assert routes() == {("langchain", "deepseek")}  # the keeper and every other role really go to DeepSeek


def test_a_model_set_in_the_shell_is_named_instead_of_claiming_success(monkeypatch):
    monkeypatch.setenv("AUTOKEEPER_MODEL", "claude-opus-5-5")
    out = CliRunner().invoke(app, ["login", "deepseek", "--key", "sk-x"])
    assert out.exit_code == 0
    assert "以后默认用" not in flat(out) and "环境变量 AUTOKEEPER_MODEL=claude-opus-5-5" in flat(out)


def test_a_model_left_in_the_key_file_is_taken_out():
    config.save_key("AUTOKEEPER_MODEL", "claude-opus-5-5")
    out = CliRunner().invoke(app, ["login", "deepseek", "--key", "sk-x"])
    assert out.exit_code == 0 and "以后默认用 deepseek" in flat(out), out.output
    assert "AUTOKEEPER_MODEL" not in (paths.USER_DIR / ".env").read_text(encoding="utf-8")
    assert routes() == {("langchain", "deepseek")}


def test_roles_pinned_in_the_config_file_are_named():
    paths.USER_DIR.mkdir(parents=True, exist_ok=True)
    (paths.USER_DIR / "config.toml").write_text('[roles.keeper]\nmodel = "claude-sonnet-5-5"\nbackend = "claude"\n', encoding="utf-8")
    out = CliRunner().invoke(app, ["login", "deepseek", "--key", "sk-x"])
    assert "[roles.keeper]" in flat(out) and "以后默认用" not in flat(out)


def test_the_game_needs_no_claude_and_says_how_to_add_it(monkeypatch):
    """The game is model-agnostic (the user, 2026-10-20): the Claude backend is an extra, and
    choosing it without the extra says how to add it."""
    import tomllib

    project = tomllib.loads((paths.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not any(d.startswith("claude-agent-sdk") for d in project["dependencies"])
    assert any(d.startswith("claude-agent-sdk") for d in project["optional-dependencies"]["claude"])
    monkeypatch.setattr(config, "claude_ready", lambda: False)
    out = CliRunner().invoke(app, ["login", "claude"], input="\n")
    assert 'autokeeper[claude]' in " ".join(out.output.split())
