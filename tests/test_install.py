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


def run(tmp_path: Path, uv_install: str, *, settings: dict | None = None, available: bool = True,
        renderer: bool = True) -> subprocess.CompletedProcess:
    bin_, tools = tmp_path / "stubs", tmp_path / "tool-bin"
    bin_.mkdir()
    tools.mkdir()
    log = tmp_path / "log"
    stub(bin_, "curl", 'case "$*" in *releases/latest*) echo https://github.com/satoshinji2992/autokeeper/releases/tag/v0.0.5; exit 0 ;; esac\n'
         + f'exit {0 if available else 22}')
    stub(bin_, "node", "exit 0")
    stub(bin_, "npm", "exit 0")
    if renderer:
        stub(bin_, "chafa", "exit 0")
    stub(bin_, "brew", f'echo "unexpected system install $*" >> {log}\nexit 1')
    stub(bin_, "git", "exit 0")
    stub(bin_, "uv", f'echo "uv $*" >> {log}\ncase "$1 $2" in\n  "tool install") {uv_install} ;;\n'
                     f'  "tool dir") echo {tools} ;;\nesac\nexit 0')
    stub(tools, "autokeeper", f'echo "autokeeper $*" >> {log}')
    env = {"PATH": str(bin_), "HOME": str(tmp_path), "AUTOKEEPER_NO_MODIFY_PATH": "1"}
    env.update(settings or {})
    return subprocess.run([shutil.which("bash"), str(SCRIPT)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


def test_the_script_installs_the_package_and_runs_setup(tmp_path):
    out = run(tmp_path, "exit 0")
    assert out.returncode == 0, out.stderr
    calls = (tmp_path / "log").read_text(encoding="utf-8")
    assert "uv tool install --force --refresh-package autokeeper --python 3.12 autokeeper==0.0.5" in calls
    assert "autokeeper setup" in calls and "装好了" in out.stdout


def test_renderer_is_prepared_by_setup_without_a_system_chafa_command(tmp_path):
    out = run(tmp_path, "exit 0", renderer=False)
    assert out.returncode == 0, out.stderr
    calls = (tmp_path / "log").read_text()
    assert "autokeeper setup" in calls
    assert "unexpected system install" not in calls
    assert "chafa 没装上" not in out.stderr


def test_a_failed_install_stops_before_setup_and_says_why(tmp_path):
    out = run(tmp_path, "exit 1")
    assert out.returncode == 1
    assert "autokeeper 没装好" in out.stderr and "装好了" not in out.stdout
    assert "autokeeper setup" not in (tmp_path / "log").read_text(encoding="utf-8")


def test_new_release_uses_its_wheel_when_pypi_does_not_have_that_version(tmp_path):
    out = run(tmp_path, "exit 0", available=False)
    assert out.returncode == 0, out.stderr
    calls = (tmp_path / "log").read_text()
    assert "releases/download/v0.0.5/autokeeper-0.0.5-py3-none-any.whl" in calls
    assert "git+" not in calls and "autokeeper==0.0.4" not in calls


@pytest.mark.parametrize("source", ["auto", "github", "git", "pypi"])
def test_explicit_version_is_not_replaced_by_main_or_latest(tmp_path, source):
    out = run(tmp_path, "exit 0", settings={"AUTOKEEPER_VERSION": "0.0.3", "AUTOKEEPER_FROM": source}, available=False)
    assert out.returncode == 0, out.stderr
    calls = (tmp_path / "log").read_text()
    assert "0.0.3" in calls and "0.0.5" not in calls and "@main" not in calls


def test_conflicting_version_and_git_ref_fail_before_installing(tmp_path):
    out = run(tmp_path, "exit 0", settings={"AUTOKEEPER_FROM": "git", "AUTOKEEPER_VERSION": "0.0.3",
                                          "AUTOKEEPER_REF": "main"})
    assert out.returncode != 0 and "不一致" in out.stderr
    assert not (tmp_path / "log").exists()


def test_github_wheel_can_be_pinned_to_the_published_checksum(tmp_path):
    out = run(tmp_path, "exit 0", settings={"AUTOKEEPER_FROM": "github", "AUTOKEEPER_VERSION": "0.0.5",
                                          "AUTOKEEPER_SHA256": "a" * 64})
    assert out.returncode == 0, out.stderr
    assert "#sha256=" + "a" * 64 in (tmp_path / "log").read_text()
