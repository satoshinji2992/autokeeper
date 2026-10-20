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
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
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
