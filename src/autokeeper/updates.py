"""Explicit release checks and upgrades for the installation running this client."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import __version__, config, paths

REPOSITORY = "https://github.com/satoshinji2992/autokeeper"
RELEASES = REPOSITORY + "/releases/latest"
API = "https://api.github.com/repos/satoshinji2992/autokeeper/releases/latest"


def version(value: str) -> tuple[int, ...]:
    """Stable release tags use two or more numeric components, including our fourth component."""
    value = value.removeprefix("v")
    if not re.fullmatch(r"\d+(?:\.\d+)+", value):
        raise ValueError("发行版版本号无效。")
    parts = tuple(map(int, value.split(".")))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


@dataclass(frozen=True)
class Release:
    version: str
    wheel: str = ""

    @property
    def page(self) -> str:
        return f"{REPOSITORY}/releases/tag/v{self.version}"

    @property
    def newer(self) -> bool:
        return version(self.version) > version(__version__)


async def latest() -> Release:
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "AutoKeeper"})
        response.raise_for_status()
        data = response.json()
    tag = str(data.get("tag_name", ""))
    version(tag)
    number = tag.removeprefix("v")
    name = f"autokeeper-{number}-py3-none-any.whl"
    expected = f"{REPOSITORY}/releases/download/{tag}/{name}"
    wheel = ""
    for asset in data.get("assets", []):
        if asset.get("name") == name and asset.get("browser_download_url") == expected:
            wheel = expected
            digest = str(asset.get("digest", ""))
            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                wheel += "#sha256=" + digest.partition(":")[2]
            break
    return Release(number, wheel)


def command(release: Release) -> list[str] | None:
    """Only update a uv-managed installed copy, retaining its selected extras and Python."""
    if not paths.INSTALLED or os.environ.get("AUTOKEEPER_GUEST") or not release.newer or not release.wheel:
        return None
    uv = shutil.which("uv")
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    if not uv or not receipt.is_file():
        return None
    try:
        tool = tomllib.loads(receipt.read_text(encoding="utf-8"))["tool"]
        requirement = next(row for row in tool["requirements"] if row.get("name") == "autokeeper")
        extras = requirement.get("extras", [])
        if not all(isinstance(extra, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", extra) for extra in extras):
            return None
    except (OSError, ValueError, KeyError, StopIteration, TypeError, AttributeError):
        return None
    package = "autokeeper" + ("[" + ",".join(extras) + "]" if extras else "")
    return [uv, "tool", "install", "--force", "--refresh-package", "autokeeper", "--python",
            f"{sys.version_info.major}.{sys.version_info.minor}", f"{package} @ {release.wheel}"]


async def install(release: Release) -> str:
    """Run the confirmed update; return an error for display, or an empty string on success."""
    if not (argv := command(release)):
        return "此安装方式需从发行页更新。"
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **config._quiet_child())
    try:
        async with asyncio.timeout(300):
            output, _ = await process.communicate()
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        lines = output.decode("utf-8", "replace").strip().splitlines()
        return "更新失败：" + (lines[-1][:200] if lines else f"退出码 {process.returncode}")
    return ""
