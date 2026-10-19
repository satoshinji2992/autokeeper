"""The game in a browser: textual-serve starts an AutoKeeper for each visitor and shows it in our
own page (page/app_index.html), dark and amber like the game, in a monospace font that keeps
Chinese in its cells, with a line on the keys a browser keeps for itself.

`python -m autokeeper.net.web HUB_PORT PORT [URL]` is a room's page (net/room.py starts it):
each visitor gets `autokeeper join` to the Hub on this machine, as a guest. It goes when its
stdin closes, and its visitors' games with it."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

from aiohttp import web
from textual_serve.server import Server

PAGE = Path(__file__).parent / "page"
SHUTDOWN = 3.0  # seconds a closing page gives its visitors' games to quit (aiohttp's own: a minute)


def command(*args: str) -> str:
    """`autokeeper <args>` with this interpreter, as the shell that textual-serve hands it to reads it."""
    argv = [sys.executable, "-m", "autokeeper", *args]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


class Page(Server):
    """textual-serve in our page; closing, it gives the open games a few seconds, not a minute."""

    def __init__(self, command: str, *, host: str = "localhost", port: int = 8000, title: str = "AutoKeeper",
                 public_url: str | None = None):
        super().__init__(command, host=host, port=port, title=title, public_url=public_url, templates_path=PAGE)

    def serve(self, debug: bool = False, *, until_eof: bool = False) -> None:
        """`until_eof`: also stop when stdin closes (a room's page; see net/room.py)."""
        make = getattr(self, "_make_app", None)
        if make is None:  # a textual-serve that builds its app another way: its own serve()
            super().serve(debug)
            return
        self.debug = debug
        self.initialize_logging()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if until_eof:
            threading.Thread(target=self._until_eof, args=(loop,), name="stdin", daemon=True).start()
        web.run_app(make(), host=self.host, port=self.port, loop=loop, shutdown_timeout=SHUTDOWN, print=None)

    def _until_eof(self, loop: asyncio.AbstractEventLoop) -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read()
        with contextlib.suppress(RuntimeError):  # the loop has already gone
            loop.call_soon_threadsafe(self.request_exit)


def main(argv: list[str] | None = None) -> None:
    from .. import utf8_stdio

    utf8_stdio()
    hub_port, port, *rest = argv if argv is not None else sys.argv[1:]
    os.environ["COLORTERM"] = "truecolor"  # the browser's terminal shows 24-bit colour; the games it starts inherit this
    os.environ["AUTOKEEPER_GUEST"] = "1"  # visitors' games run on the host's machine, but they are not the host
    page = Page(command("join", f"127.0.0.1:{hub_port}"), host="0.0.0.0", port=int(port), title="AutoKeeper 联机",
                public_url=rest[0] if rest else None)
    page.serve(until_eof=True)


if __name__ == "__main__":
    main()
