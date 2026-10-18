"""Check terminal output separately from native-library stderr in a child process."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("crash", [False, True])
def test_game_output_stays_visible_while_native_warnings_go_to_log(tmp_path, crash):
    script = """
import os
import sys
from pathlib import Path
from autokeeper import cli, config

config.SAVES = Path(sys.argv[1])
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
    assert (tmp_path / "stderr.log").read_text(encoding="utf-8") == "native warning\n"


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
    from autokeeper import config
    from autokeeper.net.hub import room_key, room_key_file

    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.delenv("AUTOKEEPER_GUEST", raising=False)
    room_key_file(8765).write_text("secret", encoding="utf-8")
    assert room_key("ws://127.0.0.1:8765/ws") == "secret"
    assert room_key("ws://26.1.2.3:8765/ws") == ""  # a friend's machine has no such file anyway
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")  # the browser seats the host command starts
    assert room_key("ws://127.0.0.1:8765/ws") == ""
