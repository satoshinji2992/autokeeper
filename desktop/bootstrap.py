"""Application-local entry point; never install dependencies on a player's computer."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path


def prepare_bridge(runtime: Path) -> None:
    """Seed the normal user-data bridge with the locked, bundled npm packages, offline."""
    from filelock import FileLock

    from autokeeper import paths

    source = runtime / "bridge"
    digest = hashlib.sha256((source / "package-lock.json").read_bytes()).hexdigest()
    paths.BRIDGE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(paths.BRIDGE.parent / ".desktop-bridge.lock"), timeout=60):
        paths.prepare()
        mark = paths.BRIDGE / ".desktop-packages"
        if paths.bridge_ready() and mark.is_file() and mark.read_text(encoding="ascii") == digest:
            return
        target = paths.BRIDGE / "node_modules"
        temporary = paths.BRIDGE / f".packages-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source / "node_modules", temporary)
            if target.exists():
                shutil.rmtree(target)
            temporary.replace(target)
            mark.write_text(digest, encoding="ascii")
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def main() -> None:
    runtime = Path(__file__).resolve().parent
    os.environ["PATH"] = os.pathsep.join((str(runtime / "node"), str(runtime / "python"), os.environ.get("PATH", "")))
    os.environ.setdefault("COLORTERM", "truecolor")
    from autokeeper import utf8_stdio

    utf8_stdio()
    prepare_bridge(runtime)
    if sys.argv[1:] == ["--self-test"]:
        # Import by path: isolated mode does not add this script's directory to sys.path.
        import runpy

        runpy.run_path(str(runtime / "selftest.py"), run_name="__main__")
    else:
        from autokeeper import main as game

        game()


if __name__ == "__main__":
    main()
