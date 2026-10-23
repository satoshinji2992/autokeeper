"""Resolve the same release on Windows without installing software or making network calls."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
SCRIPT = Path(__file__).resolve().parents[1] / "install.ps1"
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")


def run(tmp_path, *, source="auto", version="", available=True, node="ready", winget="success"):
    harness = tmp_path / "installer-test.ps1"
    harness.write_text("""param([string]$Installer)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$script:NodeState = $env:TEST_NODE
function Get-Command {
    param([string]$Name, $ErrorAction)
    if ($Name -eq 'chafa') { return $null }
    if ($Name -eq 'node' -and $script:NodeState -eq 'missing') { return $null }
    if ($Name -eq 'npm' -and $script:NodeState -in @('missing', 'no_npm')) { return $null }
    if ($Name -eq 'winget' -and $env:TEST_WINGET -eq 'unavailable') { return $null }
    Microsoft.PowerShell.Core\\Get-Command $Name -ErrorAction SilentlyContinue
}
function node {
    $global:LASTEXITCODE = 0
    if ($script:NodeState -eq 'old') { 'v16.20.2' } else { 'v22.0.0' }
}
function npm { }
function git { }
function scoop { throw 'UNEXPECTED_SCOOP_INSTALL' }
function winget {
    Write-Output ('NODE_INSTALL=' + ($args -join ' '))
    $global:LASTEXITCODE = if ($env:TEST_WINGET -eq 'fail') { 1 } else { 0 }
    if ($env:TEST_WINGET -eq 'success') { $script:NodeState = 'ready' }
}
function Invoke-RestMethod { return [PSCustomObject]@{ tag_name = 'v0.0.5' } }
function Invoke-WebRequest { if ($env:TEST_PYPI -ne '1') { throw 'Version unavailable' } }
function uv { Write-Output ('SPEC=' + $args[-1]); throw 'RELEASE_SPEC_CAPTURE' }
try { . $Installer }
catch { if ($_.Exception.Message -eq 'RELEASE_SPEC_CAPTURE') { exit 0 }; Write-Error $_; exit 1 }
""", encoding="utf-8-sig")
    env = {**os.environ, "AUTOKEEPER_FROM": source, "AUTOKEEPER_VERSION": version, "AUTOKEEPER_REF": "",
           "AUTOKEEPER_EXTRAS": "", "AUTOKEEPER_SHA256": "", "TEST_PYPI": "1" if available else "0",
           "TEST_NODE": node, "TEST_WINGET": winget}
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(harness), str(SCRIPT)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


@pytest.mark.parametrize("source,version,available,expected", [
    ("auto", "", True, "autokeeper==0.0.5"),
    ("auto", "", False, "releases/download/v0.0.5/autokeeper-0.0.5-py3-none-any.whl"),
    ("github", "0.0.3", False, "releases/download/v0.0.3/autokeeper-0.0.3-py3-none-any.whl"),
    ("git", "0.0.3", False, "git+https://github.com/satoshinji2992/autokeeper@v0.0.3"),
])
def test_windows_installer_resolves_a_release_before_native_install(tmp_path, source, version, available, expected):
    result = run(tmp_path, source=source, version=version, available=available)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert "NODE_INSTALL=" not in result.stdout
    assert "chafa 没装上" not in result.stdout


@pytest.mark.parametrize("node", ["missing", "old", "no_npm"])
def test_windows_installer_prepares_node_before_installing_autokeeper(tmp_path, node):
    result = run(tmp_path, node=node)
    assert result.returncode == 0, result.stderr
    assert "NODE_INSTALL=install --id OpenJS.NodeJS.LTS --exact --source winget" in result.stdout
    assert result.stdout.index("NODE_INSTALL=") < result.stdout.index("SPEC=autokeeper==0.0.5")


@pytest.mark.parametrize("winget", ["fail", "unchanged", "unavailable"])
def test_windows_installer_stops_when_node_is_still_unavailable(tmp_path, winget):
    result = run(tmp_path, node="missing", winget=winget)
    assert result.returncode != 0
    assert "SPEC=" not in result.stdout
    assert "Node.js" in result.stdout + result.stderr
