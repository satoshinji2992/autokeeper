"""Password-protected browser access to the local game; no changes to game configuration."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import shlex
import sys
import time
from collections import deque
from urllib.parse import urlsplit

from aiohttp import web
from textual_serve.server import Server

COOKIE = "autokeeper_access"
LIFETIME = 48 * 3600
LOGIN = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoKeeper · 进入游戏</title><style>
body{margin:0;background:#0c0a06;color:#f3d7a6;font:17px system-ui;display:grid;min-height:100dvh;place-items:center}
main{width:min(82vw,360px);padding:32px;border:1px solid #6b491a;border-radius:12px;background:#15110a}
h1{color:#ffb000;font-size:26px}p{line-height:1.7;color:#c0a783}
input,button{box-sizing:border-box;width:100%;padding:14px;margin-top:12px;border-radius:6px;font:inherit}
input{background:#0c0a06;color:#f3d7a6;border:1px solid #6b491a}
button{background:#ffb000;color:#201600;border:0;font-weight:650;cursor:pointer}
</style><main><h1>AutoKeeper</h1><p>一场属于你的调查。<br>输入访问口令，进入游戏。</p>
<form method="post" action="/login"><input type="password" name="password" placeholder="访问口令"
autocomplete="current-password" required autofocus><button>进入游戏</button></form>
<p style="font-size:13px">手机建议横屏游玩。</p></main></html>"""


class PublicServer(Server):
    def __init__(self, command: str, *, password: str, public_url: str, port: int = 8090):
        parsed = urlsplit(public_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/"):
            raise ValueError("public_url must be an origin without a path")
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Public access requires HTTPS")
        if len(password) < 16:
            raise ValueError("Access password must be at least 16 characters")
        super().__init__(command, host="127.0.0.1", port=port, public_url=public_url.rstrip("/"), title="AutoKeeper")
        self._password = password.encode()
        self._key = hashlib.sha256(b"autokeeper-cookie-v1:" + self._password).digest()
        self._failures: deque[float] = deque()
        self._sessions = 0

    def initialize_logging(self) -> None:
        # The upstream rich traceback prints local variables; credentials must stay out of logs.
        logging.basicConfig(level=logging.INFO)

    def token(self) -> str:
        body = f"{int(time.time()) + LIFETIME}.{secrets.token_hex(16)}"
        return body + "." + hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()

    def authorized(self, request: web.Request) -> bool:
        value = request.cookies.get(COOKIE, "")
        if len(value) > 256:
            return False
        try:
            expiry, nonce, signature = value.split(".")
            body = f"{expiry}.{nonce}"
            expected = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
            return time.time() < int(expiry) <= time.time() + LIFETIME + 5 and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError, UnicodeError):
            return False

    async def login(self, request: web.Request) -> web.Response:
        if request.method == "GET":
            return web.Response(text=LOGIN, content_type="text/html")
        now = time.monotonic()
        while self._failures and self._failures[0] < now - 60:
            self._failures.popleft()
        if len(self._failures) >= 10:
            raise web.HTTPTooManyRequests(text="尝试次数较多，请一分钟后再试。", headers={"Retry-After": "60"})
        form = await request.post()
        supplied = form.get("password", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), self._password):
            self._failures.append(now)
            return web.Response(text=LOGIN.replace("一场属于你的调查。", "口令不正确，请重新输入。"),
                                content_type="text/html", status=401)
        response = web.HTTPSeeOther("/")
        response.set_cookie(COOKIE, self.token(), max_age=LIFETIME, httponly=True,
                            secure=self.public_url.startswith("https://"), samesite="Strict", path="/")
        raise response

    async def _make_app(self) -> web.Application:
        app = await super()._make_app()

        @web.middleware
        async def protect(request, handler):
            if request.path in ("/login", "/healthz"):
                return await handler(request)
            if not self.authorized(request):
                if request.path == "/ws":
                    raise web.HTTPUnauthorized(text="请先登录。")
                raise web.HTTPSeeOther("/login")
            if request.path == "/ws" and request.headers.get("Origin", self.public_url) != self.public_url:
                raise web.HTTPForbidden(text="连接来源不匹配。")
            return await handler(request)

        async def headers(request, response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Content-Type-Options"] = "nosniff"

        async def health(request):
            return web.json_response({"status": "ok"})

        app.middlewares.insert(0, protect)
        app.router.add_route("*", "/login", self.login)
        app.router.add_get("/healthz", health)
        app.on_response_prepare.append(headers)
        return app

    async def handle_websocket(self, request):
        if self._sessions >= 2:
            raise web.HTTPServiceUnavailable(text="已有两个游戏窗口，请先关闭不用的窗口。")
        self._sessions += 1
        try:
            return await super().handle_websocket(request)
        finally:
            self._sessions -= 1


def main() -> None:
    from autokeeper import config, paths

    config.load_env()
    folder = paths.dev_output("public-share")
    credentials = json.loads((folder / "credentials.json").read_text())
    status = json.loads((folder / "status.json").read_text())
    argv = [sys.executable, "-m", "autokeeper", "--preset", "deepseek", "--model", "deepseek:deepseek-flash", "play", "--no-intro"]
    server = PublicServer(shlex.join(argv), password=credentials["password"],
                          public_url=status["url"], port=status["port"])
    server.serve()


if __name__ == "__main__":
    main()
