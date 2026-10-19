"""Check terminal output separately from native-library stderr in a child process."""

from __future__ import annotations

import subprocess
import sys

import pytest

from autokeeper import paths


@pytest.mark.parametrize("crash", [False, True])
def test_game_output_stays_visible_while_native_warnings_go_to_log(tmp_path, crash):
    script = """
import os
import sys
from pathlib import Path
from autokeeper import paths
from autokeeper import cli, paths

paths.DATA = Path(sys.argv[1])
original = sys.__stderr__

class App:
    return_code = 0

    def run(self):
        sys.__stderr__.write('visible game screen\\n')
        os.write(2, b'native warning\\n')
        if sys.argv[2] == 'True':
            raise RuntimeError('startup failed')

try:
    cli._run(App())
except RuntimeError:
    pass
assert sys.__stderr__ is original
os.write(2, b'after game\\n')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(crash)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "visible game screen\nafter game\n"
    assert (tmp_path / "logs" / "stderr.log").read_text(encoding="utf-8") == "native warning\n"


def test_terminals_that_show_true_colour_are_told_so(monkeypatch):
    """Windows Terminal inside WSL sets WT_SESSION but no COLORTERM: without it the photos were
    squeezed into 256 colours and blue-greys came out navy and teal."""
    import autokeeper

    for key in ("COLORTERM", "WT_SESSION", "TERM_PROGRAM", "KONSOLE_VERSION", "VTE_VERSION", "AUTOKEEPER_TRUECOLOR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert not autokeeper.truecolor() and "COLORTERM" not in __import__("os").environ
    monkeypatch.setenv("WT_SESSION", "abc")
    assert autokeeper.truecolor() and __import__("os").environ["COLORTERM"] == "truecolor"
    monkeypatch.delenv("COLORTERM")
    monkeypatch.setenv("AUTOKEEPER_TRUECOLOR", "0")
    assert not autokeeper.truecolor()


def test_version_flag_prints_the_version():
    from typer.testing import CliRunner

    from autokeeper import __version__
    from autokeeper.cli import app

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.strip() == f"autokeeper {__version__}"


def test_only_a_join_from_the_host_machine_carries_the_room_key(monkeypatch, tmp_path):
    from autokeeper.net.hub import room_key, room_key_file

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.delenv("AUTOKEEPER_GUEST", raising=False)
    room_key_file(8765).parent.mkdir(parents=True)
    room_key_file(8765).write_text("secret", encoding="utf-8")
    assert room_key("ws://127.0.0.1:8765/ws") == "secret"
    assert room_key("ws://26.1.2.3:8765/ws") == ""  # a friend's machine has no such file anyway
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")  # the browser seats the host command starts
    assert room_key("ws://127.0.0.1:8765/ws") == ""


def test_slow_things_move_once_from_the_old_saves_folder():
    (paths.OLD_SAVES / "models" / "bge").mkdir(parents=True)
    (paths.OLD_SAVES / "investigators").mkdir()
    (paths.OLD_SAVES / "investigators" / "chen.json").write_text("{}", encoding="utf-8")
    (paths.OLD_SAVES / "old-game.json").write_text("{}", encoding="utf-8")
    assert sorted(paths.adopt_old()) == ["investigators", "models"]
    assert (paths.models() / "bge").is_dir() and (paths.cards() / "chen.json").is_file()
    assert (paths.OLD_SAVES / "old-game.json").is_file() and not paths.save("old-game").exists()  # games stay behind
    assert paths.adopt_old() == []


def test_the_claude_code_plugin_has_the_package_version():
    import json

    from autokeeper import __version__

    manifest = json.loads((paths.ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == __version__
