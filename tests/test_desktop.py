"""Desktop first-run preparation stays offline, preserves data, and retries interrupted copies."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from autokeeper import paths

prepare_bridge = runpy.run_path(str(Path(__file__).resolve().parents[1] / "desktop" / "bootstrap.py"))["prepare_bridge"]


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    source = runtime / "bridge"
    package = tmp_path / "package" / "bridge"
    for folder in (source, package):
        folder.mkdir(parents=True)
        for name in ("bcdice_bridge.mjs", "package.json", "package-lock.json"):
            (folder / name).write_text(name, encoding="utf-8")
    (source / "node_modules" / "bcdice").mkdir(parents=True)
    (source / "node_modules" / "bcdice" / "package.json").write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(paths, "BUNDLE", package.parent)
    monkeypatch.setattr(paths, "BRIDGE", tmp_path / "user" / "bridge")
    return runtime


def test_desktop_first_run_prepares_dice_without_npm_or_network(desktop):
    prepare_bridge(desktop)
    assert paths.bridge_ready()
    assert paths.bridge_script().read_text(encoding="utf-8") == "bcdice_bridge.mjs"
    assert (paths.BRIDGE / "node_modules" / "bcdice" / "package.json").read_text(encoding="utf-8") == "bundled"
    # Unchanged subsequent starts must not replace a running bridge's files.
    marker = paths.BRIDGE / "node_modules" / "keep"
    marker.write_text("keep", encoding="utf-8")
    prepare_bridge(desktop)
    assert marker.exists()


def test_desktop_dependency_update_replaces_packages_but_preserves_other_user_data(desktop):
    prepare_bridge(desktop)
    save = paths.BRIDGE.parent / "games" / "game.json"
    save.parent.mkdir()
    save.write_text("saved game", encoding="utf-8")
    (paths.BRIDGE / "node_modules" / "obsolete").write_text("old", encoding="utf-8")
    (desktop / "bridge" / "package-lock.json").write_text("updated lock", encoding="utf-8")
    prepare_bridge(desktop)
    assert not (paths.BRIDGE / "node_modules" / "obsolete").exists()
    assert save.read_text(encoding="utf-8") == "saved game"


def test_interrupted_dependency_copy_keeps_the_existing_bridge_and_can_retry(desktop, monkeypatch):
    prepare_bridge(desktop)
    (desktop / "bridge" / "package-lock.json").write_text("updated lock", encoding="utf-8")
    import shutil

    original = shutil.copytree

    def interrupted(source, target, *args, **kwargs):
        Path(target).mkdir()
        raise OSError("interrupted copy")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copytree", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            prepare_bridge(desktop)
    assert paths.bridge_ready()
    assert not list(paths.BRIDGE.glob(".packages-*"))
    assert shutil.copytree is original
    prepare_bridge(desktop)
    assert paths.bridge_ready()
