"""install.sh: uv installs the command, setup runs, and a failed step stops the script with its reason.
Every program it calls is a stand-in, so nothing is downloaded."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="install.sh is for bash")


def stub(folder: Path, name: str, body: str) -> None:
    f = folder / name
    f.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    f.chmod(0o755)


def run(tmp_path: Path, uv_install: str) -> subprocess.CompletedProcess:
    bin_, tools = tmp_path / "stubs", tmp_path / "tool-bin"
    bin_.mkdir()
    tools.mkdir()
    log = tmp_path / "log"
    stub(bin_, "curl", "exit 0")  # PyPI has the package
    stub(bin_, "node", "exit 0")
    stub(bin_, "npm", "exit 0")
    stub(bin_, "uv", f'echo "uv $*" >> {log}\ncase "$1 $2" in\n  "tool install") {uv_install} ;;\n'
                     f'  "tool dir") echo {tools} ;;\nesac\nexit 0')
    stub(tools, "autokeeper", f'echo "autokeeper $*" >> {log}')
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "AUTOKEEPER_NO_MODIFY_PATH": "1"}
    return subprocess.run(["bash", str(SCRIPT)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


def test_the_script_installs_the_package_and_runs_setup(tmp_path):
    out = run(tmp_path, "exit 0")
    assert out.returncode == 0, out.stderr
    calls = (tmp_path / "log").read_text(encoding="utf-8")
    assert "uv tool install --force --refresh-package autokeeper --python 3.12 autokeeper" in calls
    assert "autokeeper setup" in calls and "装好了" in out.stdout


def test_a_failed_install_stops_before_setup_and_says_why(tmp_path):
    out = run(tmp_path, "exit 1")
    assert out.returncode == 1
    assert "autokeeper 没装好" in out.stderr and "装好了" not in out.stdout
    assert "autokeeper setup" not in (tmp_path / "log").read_text(encoding="utf-8")
