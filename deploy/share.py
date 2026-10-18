"""Start/stop only this project's temporary public gateway and tunnel."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "saves" / "public-share"
STATUS = FOLDER / "status.json"


def owned(pid: int, marker: str) -> bool:
    try:
        return marker in Path(f"/proc/{pid}/cmdline").read_bytes().decode().replace("\0", " ")
    except OSError:
        return False


def stop() -> None:
    if not STATUS.exists():
        return
    info = json.loads(STATUS.read_text())
    for name, marker in (("gateway_pid", "deploy/public_gateway.py"), ("tunnel_pid", "cloudflared")):
        pid = info.get(name, 0)
        if pid and owned(pid, marker):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    STATUS.unlink(missing_ok=True)


def start(port: int) -> None:
    FOLDER.mkdir(parents=True, exist_ok=True)
    if STATUS.exists():
        old = json.loads(STATUS.read_text())
        if owned(old.get("gateway_pid", 0), "deploy/public_gateway.py") and owned(old.get("tunnel_pid", 0), "cloudflared"):
            print(json.dumps({"url": old["url"], "status": "already running"}))
            return
        stop()
    credentials = FOLDER / "credentials.json"
    if not credentials.exists():
        fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"password": secrets.token_urlsafe(18)}, f)
    binary = FOLDER / "bin" / "cloudflared"
    if not binary.is_file():
        raise SystemExit("cloudflared is not installed in saves/public-share/bin/")
    with (FOLDER / "tunnel.log").open("w") as log:
        tunnel = subprocess.Popen([str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}",
                                   "--protocol", "http2", "--no-autoupdate"], cwd=ROOT,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    info = {"port": port, "tunnel_pid": tunnel.pid}
    STATUS.write_text(json.dumps(info))
    try:
        deadline = time.monotonic() + 60
        url = ""
        while time.monotonic() < deadline:
            found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", (FOLDER / "tunnel.log").read_text())
            if found:
                url = found.group()
                break
            if tunnel.poll() is not None:
                raise RuntimeError("Tunnel process exited; see saves/public-share/tunnel.log")
            time.sleep(0.5)
        if not url:
            raise TimeoutError("No public URL returned within 60 seconds")
        info["url"] = url
        STATUS.write_text(json.dumps(info))
        with (FOLDER / "gateway.log").open("a") as log:
            proc = subprocess.Popen([sys.executable, str(ROOT / "deploy" / "public_gateway.py")], cwd=ROOT,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        info["gateway_pid"] = proc.pid
        STATUS.write_text(json.dumps(info))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Gateway process exited; see saves/public-share/gateway.log")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                    if response.status == 200:
                        print(json.dumps({"url": url, "status": "running", "credentials": str(credentials)}))
                        return
            except OSError:
                time.sleep(0.5)
        raise TimeoutError("Gateway did not become ready")
    except BaseException:
        stop()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    if args.action == "start":
        start(args.port)
    else:
        stop()
        print("Public access stopped")
