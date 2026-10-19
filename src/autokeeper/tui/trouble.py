"""遇到问题: what to do when friends cannot get in, the page does not open, the versions differ
or the room does not open. Short numbered steps in plain words; the technical part (every
address and network, the ports, the log files, the last error) lives only here, never on the
lobby or the host screen. Opened from both (朋友进不来？ / 遇到问题); the guide has the
same page without this room's own facts."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from .. import __version__, config
from ..net import room as lan


def text(info: dict[str, Any] | None = None, *, theirs: str = "", detail: str = "", generic: bool = False) -> str:
    """The page as Markdown. `info`: the room's (Hub.room), else this machine's addresses and
    the default ports; `generic`: none of this machine's facts (the guide's copy)."""
    if generic:
        rows, port, web, hint = [], lan.HUB_PORT, lan.WEB_PORT, ""
    else:
        info = info or {}
        rows = [(str(ip), str(label)) for ip, label in info.get("addresses", [])] or lan.addresses()
        port, web = int(info.get("port") or lan.HUB_PORT), int(info.get("web") or lan.WEB_PORT)
        hint = info.get("hint", "")
    where = "\n".join(f"   - {lan.page_url(ip, web)} 或 {lan.join_target(ip, port)}（{label}）" for ip, label in rows)
    out = []
    if detail:
        out.append(f"## 刚才的错误\n\n{detail}\n")
    out.append("## 朋友进不来\n\n"
               "1. 先确认你们连的是同一个 Wi-Fi，或者同一个局域网。\n"
               "2. 不在一个地方，就一起装 Radmin VPN 或 ZeroTier，加进同一个网络，再把那边的地址发给朋友。\n"
               + (f"3. 这台电脑的地址有这些，一个不行就换一个：\n\n{where}\n\n" if where else
                  "3. 准备房间里的「朋友进不来？」会列出这台电脑的每个地址，一个不行就换一个。\n")
               + f"4. 在防火墙里允许 AutoKeeper（Python）联网。要手动放行的话，端口是 {port} 和 {web}。\n"
               "5. 在 WSL 里开房间的，要在 Windows 的 .wslconfig 里写上 networkingMode=mirrored，重启 WSL 再开。\n"
               + (f"\n{hint}\n" if hint else ""))
    out.append("## 网页打不开\n\n"
               f"1. 地址要整串复制，带上后面的 :{web}。\n"
               "2. 房主开房间时要勾上「同时开网页版」。\n"
               "3. 换一个浏览器试试，Chrome、Edge、Firefox 都行。\n"
               f"4. 还是不行，看一下网页的记录：{config.SAVES / 'web.log'}\n")
    out.append("## 版本不一样\n\n"
               "1. 大家更新到同一版：在 AutoKeeper 的文件夹里运行 git pull，再运行 uv sync。\n"
               f"2. 你的版本是 {__version__}" + (f"，房主的是 {theirs}" if theirs and theirs != __version__ else "") + "。\n")
    out.append("## 房间开不起来\n\n"
               "1. 可能已经开着一个房间：看标题画面有没有「回到房间」，或者关掉另一个开着的 AutoKeeper。\n"
               "2. 命令行开的房间（autokeeper host）也算，在那个窗口按 Ctrl+C 关掉。\n"
               f"3. 端口 {lan.HUB_PORT} 或 {lan.WEB_PORT} 被别的程序占着时，从标题画面开的房间会自己换一个；命令行用 --hub-port 和 --port 指定。\n"
               f"4. 程序的记录在 {config.SAVES / 'stderr.log'}。\n")
    return "\n".join(out)


class TroubleModal(ModalScreen[None]):
    DEFAULT_CSS = """
    #trouble { width: 100; max-width: 96%; height: 90%; }
    #trouble VerticalScroll { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "dismiss", "关闭")]

    def __init__(self, info: dict[str, Any] | None = None, *, theirs: str = "", detail: str = ""):
        super().__init__()
        self.page = text(info, theirs=theirs, detail=detail)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="trouble"):
            yield Static("遇到问题", classes="modal-title")
            with VerticalScroll():
                yield Markdown(self.page)
            yield Static("Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()
