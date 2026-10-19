"""A private loopback dashboard. Reading never calls models or pauses the game."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from . import diagnostics

if TYPE_CHECKING:
    from .game import Game

ASSETS = Path(__file__).with_name("developer_page")


class DeveloperWeb:
    def __init__(self, game: Game):
        self.game = game
        self.key = secrets.token_urlsafe(32)
        self.url = ""
        self.runner: web.AppRunner | None = None
        self.task: asyncio.Task | None = None
        self.analysis: dict = {"working": False, "question": "", "answer": "", "error": ""}
        self.errors: list[str] = []
        self.busy = False

    async def start(self) -> str:
        if self.runner is not None:
            return self.url
        if getattr(self.game, "debug_journal", None) is None:
            diagnostics.attach(self.game)
        app = web.Application(middlewares=[self._private], client_max_size=20_000)
        prefix = f"/{self.key}"
        app.router.add_get(prefix + "/", self._page)
        app.router.add_get(prefix + "/app.js", self._asset)
        app.router.add_get(prefix + "/style.css", self._asset)
        app.router.add_get(prefix + "/api/snapshot", self._snapshot)
        app.router.add_get(prefix + "/api/records", self._records)
        app.router.add_get(prefix + "/api/analysis", self._analysis)
        app.router.add_post(prefix + "/api/analysis", self._ask)
        app.router.add_get(prefix + "/api/export", self._export)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/{self.key}/"
        return self.url

    @web.middleware
    async def _private(self, request: web.Request, handler):
        remote = request.remote
        try:
            local = ipaddress.ip_address(remote).is_loopback if remote else False
        except ValueError:
            local = False
        if not local or request.match_info.http_exception is not None:
            raise web.HTTPNotFound()
        # Do not permit another website to reach local GM material through DNS rebinding
        # or to trigger paid diagnostic calls. No CORS headers are issued.
        expected = self.url.split("/", 3)[2] if self.url else request.host
        if request.host != expected:
            raise web.HTTPForbidden(text="仅限本机访问")
        origin = request.headers.get("Origin")
        if ((origin and origin != f"http://{expected}") or
                request.headers.get("Sec-Fetch-Site") == "cross-site"):
            raise web.HTTPForbidden(text="不接受其他网页的请求")
        response = await handler(request)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    async def _page(self, request: web.Request) -> web.Response:
        return web.Response(text=(ASSETS / "index.html").read_text(encoding="utf-8"), content_type="text/html")

    async def _asset(self, request: web.Request) -> web.Response:
        name = request.path.rsplit("/", 1)[-1]
        return web.Response(text=(ASSETS / name).read_text(encoding="utf-8"),
                            content_type="text/javascript" if name.endswith(".js") else "text/css")

    async def _snapshot(self, request: web.Request) -> web.Response:
        data = diagnostics.snapshot(self.game, errors=self.errors, busy=self.busy, include_calls=False)
        data["analysis"] = self.analysis
        return web.json_response(data)

    async def _records(self, request: web.Request) -> web.Response:
        try:
            after = max(0, int(request.query.get("after", "0")))
            limit = max(1, min(250, int(request.query.get("limit", "100"))))
        except ValueError as exc:
            raise web.HTTPBadRequest(text="记录范围无效") from exc
        if request.query.get("direction") == "older":
            rows, cursor = await asyncio.to_thread(self.game.debug_journal.recent, before=after, limit=limit,
                                                   kind=request.query.get("kind", ""))
        else:
            rows, cursor = await asyncio.to_thread(self.game.debug_journal.read, after=after, limit=limit,
                                                   kind=request.query.get("kind", ""))
        return web.json_response({"records": rows, "cursor": cursor, "more": len(rows) == limit})

    async def _analysis(self, request: web.Request) -> web.Response:
        return web.json_response(diagnostics.redact(self.analysis))

    async def _ask(self, request: web.Request) -> web.Response:
        if self.task is not None and not self.task.done():
            raise web.HTTPConflict(text="当前排查尚未结束")
        try:
            data = await request.json()
            question = data.get("question", "").strip()
            start = int(data["from_turn"]) if data.get("from_turn") is not None else None
            end = int(data["to_turn"]) if data.get("to_turn") is not None else None
        except (ValueError, AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise web.HTTPBadRequest(text="排查请求格式无效") from exc
        if not question or len(question) > 4000 or (start is not None and start < 0) or (end is not None and end < 0):
            raise web.HTTPBadRequest(text="问题或回合范围无效")
        if start is not None and end is not None and start > end:
            raise web.HTTPBadRequest(text="起始回合不能晚于结束回合")
        data = diagnostics.snapshot(self.game, errors=self.errors, busy=self.busy)
        records, _ = await asyncio.to_thread(self.game.debug_journal.read)
        selected, scope = diagnostics.select_evidence(self.game, question, records, start=start, end=end)
        data["history"] = selected
        data["evidence_scope"] = scope
        if start is not None or end is not None:
            data["calls"] = [r["data"] for r in selected if r.get("kind") == "call"]
        self.analysis = {"working": True, "question": diagnostics.redact(question), "answer": "", "error": "",
                         "from_turn": start, "to_turn": end, "evidence_entries": len(selected), "evidence_scope": scope}
        self.task = asyncio.create_task(self._run(data, question), name="developer-diagnostic")
        return web.json_response(self.analysis, status=202)

    async def _run(self, data: dict, question: str) -> None:
        async def delta(piece: str) -> None:
            self.analysis["answer"] += diagnostics.redact(piece)

        journal = self.game.debug_journal
        journal.append("diagnostic", {"status": "requested", **self.analysis}, turn=self.game.state.turn)
        try:
            answer = await diagnostics.analyze(self.game, data, question, on_delta=delta)
            self.analysis["answer"] = diagnostics.redact(answer)
        except asyncio.CancelledError:
            self.analysis["error"] = "排查已随本局关闭而取消"
            raise
        except Exception as exc:
            self.analysis["error"] = diagnostics.redact(f"{type(exc).__name__}: {exc}")
        finally:
            self.analysis["working"] = False
            journal.append("diagnostic", {"status": "finished", **self.analysis}, turn=self.game.state.turn)

    async def _export(self, request: web.Request) -> web.Response:
        records, _ = await asyncio.to_thread(self.game.debug_journal.read)
        data = diagnostics.snapshot(self.game, errors=self.errors, busy=self.busy, include_calls=False)
        # Download all entries, not just the page currently displayed in the browser.
        return web.Response(text=json.dumps({**data, "records": records, "analysis": self.analysis},
                                             ensure_ascii=False, indent=2), content_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="autokeeper-diagnostics.json"'})

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None


async def open_dashboard(game: Game, *, errors: list[str] | None = None, busy: bool = False) -> str:
    server = getattr(game, "debug_server", None)
    if server is None:
        game.debug_server = server = DeveloperWeb(game)
    server.errors = errors if errors is not None else []
    server.busy = busy
    return await server.start()
