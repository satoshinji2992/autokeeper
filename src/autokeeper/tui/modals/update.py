"""Manually check a release and update a supported client installation."""

from __future__ import annotations

import os

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ... import __version__, paths, updates


class UpdateModal(ModalScreen):
    CSS_PATH = "update.tcss"
    BINDINGS = [Binding("escape", "close", "关闭")]

    def __init__(self) -> None:
        super().__init__()
        self.release: updates.Release | None = None
        self.installing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="update-dialog", classes="modal"):
            yield Static("软件更新", classes="modal-title")
            yield Static(f"当前版本  {__version__}", id="update-version")
            yield Static("正在检查最新发行版…", id="update-status", markup=False)
            yield Static("", id="update-method", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("检查更新", id="update-check")
                yield Button("更新", id="update-install", variant="primary", disabled=True)
                yield Button("发行页", id="update-page")
                yield Button("关闭", id="update-close")

    def on_mount(self) -> None:
        self.check()

    @work(exclusive=True, group="release-check")
    async def check(self) -> None:
        self.query_one("#update-check", Button).disabled = True
        self.query_one("#update-install", Button).disabled = True
        self.query_one("#update-status", Static).update("正在检查最新发行版…")
        try:
            self.release = await updates.latest()
        except Exception:
            self.release = None
            self.query_one("#update-status", Static).update("暂时无法检查更新，请重试或打开发行页。")
        else:
            latest = self.release
            self.query_one("#update-status", Static).update(
                f"发现新版本  {latest.version}" if latest.newer else f"已是最新版本（发行版 {latest.version}）。")
            room = getattr(self.app, "room", None)
            hosting = bool(room is not None and room.is_open)
            supported = updates.command(latest) is not None and not hosting
            self.query_one("#update-install", Button).disabled = not supported
            method = ("房间仍在运行，请关闭房间后更新。" if hosting else
                      "网页版使用房主版本，请由房主更新客户端。" if os.environ.get("AUTOKEEPER_GUEST") else
                      "当前从源码运行，请在仓库中更新，或从发行页下载客户端。" if not paths.INSTALLED else
                      "更新后退出并重新打开程序。存档与设置保留。" if supported else
                      "可从发行页下载新版，或重新运行原安装程序。" if latest.newer else "")
            self.query_one("#update-method", Static).update(method)
        finally:
            self.query_one("#update-check", Button).disabled = False

    @on(Button.Pressed, "#update-check")
    def recheck(self) -> None:
        self.check()

    @on(Button.Pressed, "#update-install")
    def confirm(self) -> None:
        from .menu import Confirm

        if self.release is not None and not self.installing:
            self.app.push_screen(Confirm(f"更新到 {self.release.version}？", [("yes", "更新"), ("no", "取消")]),
                                 lambda answer: self.install() if answer == "yes" else None)

    @work(exclusive=True, group="release-install")
    async def install(self) -> None:
        if self.release is None:
            return
        self.installing = True
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#update-status", Static).update("正在安装更新…")
        try:
            problem = await updates.install(self.release)
        except TimeoutError:
            problem = "更新超时，请检查网络后重试。"
        except OSError:
            problem = "无法启动更新程序，请从发行页更新。"
        self.query_one("#update-status", Static).update(problem or "更新完成。退出并重新打开程序后生效。")
        self.installing = False
        for name in ("check", "page", "close"):
            self.query_one(f"#update-{name}", Button).disabled = False

    @on(Button.Pressed, "#update-page")
    def page(self) -> None:
        self.app.open_url(self.release.page if self.release is not None else updates.RELEASES)

    @on(Button.Pressed, "#update-close")
    def action_close(self) -> None:
        if not self.installing:
            self.dismiss()
