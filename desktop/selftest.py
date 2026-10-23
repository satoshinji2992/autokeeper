"""Offline release smoke test, also available as AutoKeeper.exe --self-test."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


async def interface() -> None:
    from autokeeper.tui.app import AutoKeeperApp

    app = AutoKeeperApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        assert app.screen.__class__.__name__ == "TitleScreen"
        if folder := os.environ.get("AUTOKEEPER_DESKTOP_SCREENSHOTS"):
            app.save_screenshot("desktop-title.svg", path=folder)
        await pilot.press("f9")
        await pilot.pause(0.2)
        assert app.screen.__class__.__name__ != "TitleScreen"
        if folder:
            app.save_screenshot("desktop-settings.svg", path=folder)


async def browser() -> None:
    from textual_serve.app_service import AppService
    from textual_serve.download_manager import DownloadManager

    from autokeeper.net.web import command

    received = bytearray()

    async def write_bytes(data):
        received.extend(data)

    async def ignore(*args):
        pass

    service = AppService(command("play", "--no-intro"), write_bytes=write_bytes, write_str=ignore,
                         close=ignore, download_manager=DownloadManager())
    try:
        await service.start(120, 40)
        async with asyncio.timeout(30):
            while "新的调查".encode() not in received:
                await asyncio.sleep(0.1)
    finally:
        await asyncio.wait_for(service.stop(), timeout=15)
        if process := service._process:
            if process.stdin:
                process.stdin.close()
            await asyncio.wait_for(process.wait(), timeout=10)
            await asyncio.sleep(0.1)


async def room_connection() -> None:
    from aiohttp import web

    from autokeeper.net.link import Link
    from autokeeper.net.web import Page

    async def hub(request):
        socket = web.WebSocketResponse(protocols=("autokeeper",))
        await socket.prepare(request)
        assert (await socket.receive_json())["t"] == "hello"
        await socket.send_json({"t": "welcome"})
        assert (await socket.receive_json())["t"] == "sit"
        await socket.send_json({"t": "seated"})
        async for _ in socket:
            pass
        return socket

    app = web.Application()
    app.router.add_get("/ws", hub)
    direct = web.AppRunner(app)
    await direct.setup()
    site = web.TCPSite(direct, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    page = web.AppRunner(await Page("unused", hub_port=port)._make_app())
    link = None
    try:
        await page.setup()
        site = web.TCPSite(page, "127.0.0.1", 0)
        await site.start()
        link = Link(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", "自检")
        await link.connect()
        assert (await link.ws.receive_json(timeout=5))["t"] == "welcome"
        await link.send({"t": "sit"})
        assert (await link.ws.receive_json(timeout=5))["t"] == "seated"
    finally:
        if link:
            await link.close()
        await page.cleanup()
        await direct.cleanup()


def main() -> None:
    import ctypes

    import onnxruntime
    from PIL import Image

    from autokeeper import __version__, imagery, paths
    from autokeeper.rules.bcdice import BCDice

    # Document import uses ONNX; fresh computers may have no global VC runtime.
    assert onnxruntime.get_available_providers()
    for name in ("msvcp140.dll", "msvcp140_1.dll"):
        dll = ctypes.WinDLL(name)
        filename = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(dll._handle), filename, len(filename))
        assert Path(filename.value).is_relative_to(Path(sys.prefix)), filename.value
    assert paths.INSTALLED, "The portable application must use installed-package data paths"
    assert paths.bridge_ready()
    modules = paths.module_folders()
    assert {p.name for p in modules} >= {"zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin"}
    assert imagery.available(), "Bundled picture renderer failed to load"
    picture = imagery.draw(Image.new("RGB", (32, 32), "red"), 12, 6, imagery.STYLES["photo"], "full", 5)
    assert picture and "\x1b[" in picture
    with tempfile.TemporaryDirectory(prefix="autokeeper-photo-") as folder:
        source = Path(folder) / "头像.png"
        Image.new("RGB", (30, 80), "red").save(source)
        old = imagery.preprocess(source, "portrait")
        previous = imagery.render(str(source), 30, 14, face=True)
        # An open Windows PNG must not prevent selection or a fresh portrait from loading.
        with old.open("rb"):
            Image.new("RGB", (30, 80), "blue").save(source)
            fresh = imagery.preprocess(source, "portrait")
            assert fresh != old
            assert imagery.render(str(source), 30, 14, face=True) != previous
    dice = BCDice()
    try:
        assert 1 <= dice.total("1D6") <= 6
        assert dice.check(50).level is not None
    finally:
        dice.close()
    # Browser guests and plugin sandboxes must be able to start the shipped interpreter.
    result = subprocess.run([sys.executable, "-I", "-m", "autokeeper", "--version"],
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert __version__ in result.stdout
    worker = Path(paths.PACKAGE / "plugins" / "worker.py")
    request = {"code": 'def handle(event, context, api):\n    return {"state": {"ok": True}}', "event": {}, "context": {}}
    result = subprocess.run([sys.executable, "-I", "-S", str(worker)], input=json.dumps(request) + "\n",
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert json.loads(result.stdout)["output"]["state"]["ok"]
    asyncio.run(interface())
    asyncio.run(browser())
    asyncio.run(room_connection())
    print(json.dumps({"version": __version__, "python": sys.version.split()[0],
                      "node": subprocess.check_output(["node", "--version"], text=True).strip(),
                      "modules": [p.name for p in modules], "pictures": True, "dice": True,
                      "plugins": True, "title_and_settings": True, "browser_child": True, "windows_photo_cache": True,
                      "room_url_connection": True,
                      "dependencies": len(list(importlib.metadata.distributions()))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
