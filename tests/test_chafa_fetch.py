"""chafa where the system has none (imagery.fetch_chafa): `uv tool install` cannot bring a program,
so the game fetches the official static build into its own tools folder (the user, 2026-10-20:
Windows had no chafa after `uv tool install autokeeper`). Offline: the archives are made here."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from autokeeper import imagery, paths


def fake_download(archive: bytes):
    def fetch(url, target):
        assert url.startswith("https://hpjansson.org/chafa/releases/static/chafa-")
        with open(target, "wb") as f:
            f.write(archive)
    return fetch


@pytest.fixture
def nowhere(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "tools", lambda: tmp_path / "tools")
    monkeypatch.setattr(imagery.shutil, "which", lambda name: None)
    monkeypatch.setattr(imagery, "_library", lambda: None)  # no chafa.py either: the program is the way
    return tmp_path / "tools"


def test_windows_gets_the_official_exe_from_its_zip(monkeypatch, nowhere):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("chafa-1.18.3-1-x86_64-win/README.TXT", "readme")
        z.writestr("chafa-1.18.3-1-x86_64-win/Chafa.exe", b"MZ-exe")
    monkeypatch.setattr(imagery.sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setattr("urllib.request.urlretrieve", fake_download(buf.getvalue()))
    assert not imagery.available()
    got = imagery.fetch_chafa()
    assert got == str(nowhere / "chafa.exe") and (nowhere / "chafa.exe").read_bytes() == b"MZ-exe"
    assert imagery.chafa_path() == got and imagery.available()


def test_linux_gets_the_binary_from_its_tarball_and_macos_is_told_to_brew(monkeypatch, nowhere):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"\x7fELF"
        info = tarfile.TarInfo("chafa-1.18.3-1-x86_64-linux-gnu/chafa")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(imagery.sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("urllib.request.urlretrieve", fake_download(buf.getvalue()))
    assert imagery.fetch_chafa() == str(nowhere / "chafa") and (nowhere / "chafa").read_bytes() == b"\x7fELF"
    monkeypatch.setattr(imagery.sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert imagery.fetch_chafa() == "" and "brew install chafa" in imagery.install_hint()


def test_a_chafa_that_probes_the_terminal_is_told_not_to(monkeypatch, tmp_path):
    """chafa 1.16 and later query the terminal they run in, even with their output piped (the user,
    2026-10-20: on Windows the game went black). It is called with --probe off; older ones as before."""
    def chafa(text: str):
        exe = tmp_path / f"chafa-{len(text)}"
        exe.write_text(f"#!/bin/sh\necho '{text}'\n", encoding="utf-8")
        exe.chmod(0o755)
        return str(exe)

    if imagery.sys.platform == "win32":
        pytest.skip("a shell script stands in for chafa")
    imagery._quiet.cache_clear()
    assert imagery._quiet(chafa("  --probe=ARG    Probe the terminal")) == ("--probe", "off")
    assert imagery._quiet(chafa("  --polite=BOOL  Polite mode")) == ()
    assert imagery._quiet(str(tmp_path / "missing")) == ()


def test_the_library_draws_without_a_program_and_the_switch_turns_it_off(monkeypatch, tmp_path):
    """chafa.py carries the library in its wheel (Windows too): pictures come out with no chafa
    program anywhere, and nothing runs beside the game (the user, 2026-10-20: Windows went black)."""
    from PIL import Image

    if imagery._library() is None:
        pytest.skip("chafa.py has no wheel for this platform")
    monkeypatch.setattr(imagery.shutil, "which", lambda name: None)
    monkeypatch.setattr(paths, "tools", lambda: tmp_path / "none")
    monkeypatch.setattr(imagery.subprocess, "run", lambda *a, **k: pytest.fail("no program should run"))
    im = Image.new("RGB", (120, 80), (200, 60, 40))
    out = imagery._chafa(im, 20, 6, "photo")
    assert out.count("\n") == 5 and "\x1b[" in out and imagery.available()
    monkeypatch.setenv("AUTOKEEPER_CHAFA", "off")
    assert not imagery.available() and imagery.draw(im, 20, 6, imagery.STYLES["photo"], "240", 9) is None
