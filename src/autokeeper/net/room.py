"""A LAN room on this machine. The title screen's 开房间 and `autokeeper host` both open one: the
Hub in a thread with an event loop of its own, so the game's turns and model calls never hold up
the screen that opened it, and the page that browsers open (net/web.py) in a process of its own,
which goes when its stdin closes. Closing the room stops both.

Also here: the addresses friends can reach this machine on, with the network each one is on,
the LAN saves to go on with, and the last room's settings."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from .. import config

if TYPE_CHECKING:
    from ..engine.state import GameState
    from .hub import Hub

HUB_PORT, WEB_PORT = 8765, 8000

# Virtual LANs, told apart by the adapter's name, then by the range they hand out.
NAMED = (("radmin", "Radmin VPN"), ("zerotier", "ZeroTier"), ("tailscale", "Tailscale"), ("hamachi", "Hamachi"),
         ("wireguard", "WireGuard"), ("openvpn", "OpenVPN"))
RANGES = (("26.0.0.0/8", "Radmin VPN"), ("25.0.0.0/8", "Hamachi"), ("100.64.0.0/10", "Tailscale"))
# Adapters nobody else can reach: containers, virtual machines, a proxy's TUN.
UNREACHABLE = ("docker", "br-", "veth", "virbr", "vmnet", "vboxnet", "virtualbox", "vethernet", "vmware", "loopback",
               "clash", "mihomo", "v2ray", "xray", "sing-box")
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")  # what proxy TUNs answer from


class RoomError(Exception):
    """The room did not open: a seat plan that makes no sense, a port already taken."""


# -- addresses ------------------------------------------------------------------------------
def network(adapter: str, ip: str) -> str | None:
    """What network an address is on, as a player would call it; None when nobody else can
    reach it (loopback, link-local, containers and virtual machines on this computer)."""
    name = adapter.lower()
    addr = ipaddress.ip_address(ip)
    if addr.is_loopback or addr.is_link_local or addr in FAKE_IP or name.split()[:1] in (["lo"], ["lo0"]):
        return None
    if any(word in name for word in UNREACHABLE):
        return None
    for word, label in NAMED:
        if word in name:
            return label
    if name.startswith(("zt", "feth")):  # ZeroTier on Linux and macOS
        return "ZeroTier"
    for block, label in RANGES:
        if addr in ipaddress.ip_network(block):
            return label
    return "局域网" if addr.is_private else "公网"


def _outgoing() -> str:
    """The address the default route goes out of; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


def _adapters() -> list[tuple[str, str]]:
    """(adapter name, IPv4 address) for every adapter; without ifaddr, what the resolver knows."""
    try:
        import ifaddr

        return [(f"{a.name} {a.nice_name}", ip.ip) for a in ifaddr.get_adapters() for ip in a.ips if isinstance(ip.ip, str)]
    except Exception:  # an adapter ifaddr cannot read, or no ifaddr at all
        found = {_outgoing()}
        with contextlib.suppress(OSError):
            found.update(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        return [("", ip) for ip in found if ip]


def addresses() -> list[tuple[str, str]]:
    """(address, network) for every network friends can reach this machine on: the default
    route's first, then the LAN, then virtual LANs (Radmin VPN, ZeroTier ...)."""
    first = _outgoing()
    found: dict[str, str] = {}
    for adapter, ip in _adapters():
        if ip == first:  # the default route's, even on a bridge (a Hyper-V external switch)
            adapter = ""
        if (label := network(adapter, ip)) and ip not in found:
            found[ip] = label
    rows = sorted(found.items(), key=lambda row: (row[0] != first, row[1] != "局域网", row[1] == "公网", row[0]))
    return rows or [("127.0.0.1", "本机")]


def wsl_hint(rows: list[tuple[str, str]]) -> str:
    """Inside WSL without mirrored networking only this Windows machine can reach the room."""
    if "microsoft" not in platform.uname().release.lower():
        return ""
    if all(ip == "127.0.0.1" or ipaddress.ip_address(ip) in ipaddress.ip_network("172.16.0.0/12") for ip, _ in rows):
        return "WSL 里开的房间别的电脑连不进来，要在 Windows 的 .wslconfig 里设 networkingMode=mirrored"
    return ""


def round_size(players: int) -> int:
    """Actions a round takes at a table of this many players: one each, and one more for
    whoever asks first (net/hub.py)."""
    return players + 1 if players else 0


def join_target(ip: str, port: int) -> str:
    """What to type after `autokeeper join`, or in 加入房间."""
    return ip if port == HUB_PORT else f"{ip}:{port}"


def page_url(ip: str, port: int) -> str:
    return f"http://{ip}" + ("" if port == 80 else f":{port}")


# -- saves and settings -----------------------------------------------------------------------
def lan_saves(module_id: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """LAN games kept in saves/index.json (slots named lan-*), newest first."""
    try:
        index = json.loads((config.SAVES / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [(slot, meta) for slot, meta in index.items() if slot.startswith("lan-") and isinstance(meta, dict)
            and (config.SAVES / f"{slot}.json").is_file() and module_id in (None, meta.get("module"))]
    return sorted(rows, key=lambda row: str(row[1].get("updated", "")), reverse=True)


def saved(slot: str) -> GameState:
    from ..engine.state import GameState

    return GameState.model_validate_json((config.SAVES / f"{slot}.json").read_text(encoding="utf-8"))


def recall() -> dict[str, Any]:
    """The last room opened or joined on this machine: name, address, ports, page on or off."""
    try:
        data = json.loads((config.USER_DIR / "lan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember(**changes: Any) -> None:
    with contextlib.suppress(OSError):
        config.USER_DIR.mkdir(parents=True, exist_ok=True)
        target = config.USER_DIR / "lan.json"
        target.write_text(json.dumps(recall() | changes, ensure_ascii=False, indent=1), encoding="utf-8")


def port_free(port: int) -> bool:
    """Nothing listens on it yet (the page's port is checked before its process starts)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":  # as aiohttp binds: a port just let go of is free again
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


# -- the room -------------------------------------------------------------------------------------
class Room:
    """One room on this machine: open() the Hub, start_web() the page, close() both."""

    def __init__(
        self,
        module_dir: Path,
        *,
        seats: list[str] | None = None,
        state: GameState | None = None,
        port: int = HUB_PORT,
        web_port: int = WEB_PORT,
        host: str = "0.0.0.0",
        cast: Any = None,
        rules: Any = None,
    ):
        self.dir, self.seats, self.state, self.cast, self.rules = Path(module_dir), seats, state, cast, rules
        self.host, self.port, self.web_port = host, port, web_port
        self.hub: Hub | None = None
        self.page: subprocess.Popen | None = None
        self.error: BaseException | None = None
        self.shared: dict[str, Any] = {}  # what every lobby shows (Hub.room)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.hub is not None and bool(self.hub.port)

    @property
    def title(self) -> str:
        return self.hub.m.title if self.hub else ""

    def open(self, timeout: float = 60.0) -> Hub:
        """Start the Hub and wait until it listens. Raises RoomError."""
        self._thread = threading.Thread(target=self._run, name="hub", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.close()
            raise RoomError(f"房间 {timeout:.0f} 秒还没开好")
        if self.error is not None or self.hub is None:
            error, built = self.error, self.hub is not None
            self.close()
            if built and isinstance(error, OSError):  # the module loaded: it is the port
                raise RoomError(f"联机端口 {self.port} 用不了，可能已经开着一个房间（{error.strerror or error}）") from error
            raise RoomError(str(error) or type(error).__name__) from error
        return self.hub

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:  # closed
            pass
        except Exception as exc:  # the seat plan, the module, the port
            self.error = exc
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        from .hub import Hub

        self._loop, self._main = asyncio.get_running_loop(), asyncio.current_task()
        self.hub = hub = Hub(self.dir, seats=self.seats, state=self.state, cast=self.cast, rules=self.rules)
        serving = asyncio.create_task(hub.serve(self.host, self.port))
        try:
            while not hub.port:
                if serving.done():
                    await serving  # it could not listen: the error goes to open()
                    return
                await asyncio.sleep(0.02)
            self.port = hub.port
            rows = addresses()
            self.shared.update(addresses=[list(row) for row in rows], port=self.port, web=0, hint=wsl_hint(rows))
            hub.room = self.shared
            self._ready.set()
            await serving
        finally:
            if not serving.done():
                serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)

    def start_web(self, log: IO | None = None, timeout: float = 30.0) -> str:
        """Start the page for browsers and wait until it answers; its URL. `log`: where its
        output goes (the console when None). Raises RoomError."""
        assert self.hub is not None, "open() first"
        if not port_free(self.web_port):
            raise RoomError(f"网页端口 {self.web_port} 被占用了")
        url = page_url(addresses()[0][0], self.web_port)
        argv = [sys.executable, "-m", "autokeeper.net.web", str(self.port), str(self.web_port), url]
        env = os.environ | {"PYTHONIOENCODING": "utf-8"}
        # Out of the console's process group: Ctrl+C is the room's to handle, and it closes the page.
        alone: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        self.page = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT if log else None, env=env, **alone)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.page.poll() is not None:
                raise RoomError(f"网页版没开起来（退出码 {self.page.returncode}），记录在 {config.SAVES / 'web.log'}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.web_port), 0.3):
                self.shared["web"] = self.web_port
                return url
            time.sleep(0.1)
        raise RoomError(f"网页版 {timeout:.0f} 秒还没开好")

    def wait(self) -> None:
        """Until the room closes; Ctrl+C gets through."""
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(0.5)

    def close(self, timeout: float = 10.0) -> None:
        """Stop the page, then the Hub (which closes everyone's connection). Safe to call twice."""
        page, self.page = self.page, None
        if page is not None:
            _stop(page)
        loop, main = self._loop, self._main
        if loop is not None and main is not None and not main.done():
            with contextlib.suppress(RuntimeError):  # the loop closed just now
                loop.call_soon_threadsafe(main.cancel)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)


def _stop(page: subprocess.Popen) -> None:
    """The page goes when its stdin closes (net/web.py), its visitors' games with it."""
    if page.poll() is None:
        with contextlib.suppress(OSError):
            if page.stdin:
                page.stdin.close()
        try:
            page.wait(10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(page.pid)], capture_output=True)
            else:
                page.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                page.wait(5)
    elif page.stdin:
        with contextlib.suppress(OSError):
            page.stdin.close()
