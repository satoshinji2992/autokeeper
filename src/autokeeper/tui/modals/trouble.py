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

from ... import __version__, paths
from ...net import room as lan


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
        out.append(f"## 错误详情\n\n{detail}\n")
    out.append("## 无法加入房间\n\n"
               "1. 确认双方设备连接至同一 Wi-Fi 或局域网。\n"
               "2. 异地联机可使用 Radmin VPN 或 ZeroTier，双方加入同一虚拟局域网后，分享对应的房间地址。\n"
               + (f"3. 本机可用地址如下，请选择双方均可访问的网络：\n\n{where}\n\n" if where else
                  "3. 准备房间中的「联机故障排查」列出本机各网络地址，请选择对应的局域网地址。\n")
               + f"4. 在防火墙中允许 AutoKeeper（Python）联网。手动配置时需放行端口 {port} 和 {web}。\n"
               "5. 使用 WSL 创建房间时，在 Windows 的 .wslconfig 中设置 networkingMode=mirrored，重启 WSL 后重试。\n"
               + (f"\n{hint}\n" if hint else ""))
    out.append("## 无法打开网页\n\n"
               f"1. 复制完整访问地址，包括端口 :{web}。\n"
               "2. 房主创建房间时需勾选「同时开网页版」。\n"
               "3. 可更换为 Chrome、Edge 或 Firefox 重试。\n"
               f"4. 若仍无法访问，请查看网页服务日志：{paths.web_log()}\n")
    out.append("## 版本不一致\n\n"
               "1. 双方需更新至同一版本：在 AutoKeeper 目录中依次运行 git pull 和 uv sync。\n"
               f"2. 本机版本为 {__version__}" + (f"，房主版本为 {theirs}" if theirs and theirs != __version__ else "") + "。\n")
    out.append("## 无法选择角色\n\n"
               "1. 入座后按 R 选择角色，游戏开始后不可更换。\n"
               "2. 继续存档时沿用原有调查员，不支持更换。\n"
               "3. 创建角色使用房主的模型配置，需确认配置有效且预算尚未耗尽。达到每分钟请求上限后，需等待后重试。\n"
               "4. 导入文件超过传输限制时，请缩小文件或改为粘贴文字。\n")
    out.append("## 无法创建房间\n\n"
               "1. 检查是否已有房间运行：标题页出现「回到房间」时可直接返回，或关闭其他 AutoKeeper 进程后重试。\n"
               "2. 通过 autokeeper host 创建的房间也会占用端口，可在对应终端按 Ctrl+C 停止。\n"
               f"3. 端口 {lan.HUB_PORT} 或 {lan.WEB_PORT} 被其他程序占用时，从标题页创建的房间会自动更换端口；命令行可用 --hub-port 和 --port 指定。\n"
               f"4. 程序日志位于 {paths.stderr_log()}。\n")
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
