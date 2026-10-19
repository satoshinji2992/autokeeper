"""Where the tokens went: this game by agent, turn by turn, and every game on this machine."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...engine.state import GameState
from ...harness import spend

AGENTS = {
    "keeper": "守秘人", "arbiter": "裁定员", "intent": "解析行动", "npc": "NPC", "investigator": "AI 调查员",
    "director": "导演", "chronicler": "记录员", "summarizer": "摘要员", "lore": "知识库", "vision": "读图",
    "card": "车卡", "importer": "导入", "artist": "美术", "progress": "进展", "visual:imagegen": "AI 画图",
}  # fmt: skip
BARS = "▁▂▃▄▅▆▇█"
SHOWN_TURNS = 40


def tokens(n: float) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{int(n)}"


def spark(values: list[float]) -> str:
    """One block per turn, as tall as its share of the busiest turn shown."""
    top = max(values, default=0) or 1
    return "".join(BARS[min(len(BARS) - 1, int(v / top * (len(BARS) - 1) + 0.5))] for v in values)


def usage_view(st: GameState) -> Group:
    table = Table(box=None, padding=(0, 2), show_edge=False)
    for name, justify in (("", "left"), ("调用", "right"), ("输入", "right"), ("缓存命中", "right"), ("输出", "right"), ("耗时", "right"), ("花费", "right")):
        table.add_column(name, justify=justify, header_style="dim")  # type: ignore[arg-type]
    total = dict.fromkeys(("calls", "input", "cache_read", "output", "seconds", "cost_usd"), 0.0)
    for role, u in sorted(st.usage.items(), key=lambda kv: -(kv[1].get("input", 0) + kv[1].get("output", 0))):
        for k in total:
            total[k] += u.get(k, 0)
        hit = f"{u.get('cache_read', 0) / u['input']:.0%}" if u.get("input") else "—"
        table.add_row(
            AGENTS.get(role, role), str(int(u.get("calls", 0))), tokens(u.get("input", 0)), hit, tokens(u.get("output", 0)),
            f"{u.get('seconds', 0):.0f}s", f"${u.get('cost_usd', 0):.4f}",
        )  # fmt: skip
    hit = f"{total['cache_read'] / total['input']:.0%}" if total["input"] else "—"
    table.add_row(
        Text("合计", style="bold"), str(int(total["calls"])), tokens(total["input"]), hit, tokens(total["output"]),
        f"{total['seconds']:.0f}s", f"${total['cost_usd']:.4f}", style="bold",
    )  # fmt: skip

    parts: list = [table]
    turns = st.turn_usage[-SHOWN_TURNS:]
    if turns:
        line = Text("\n每回合的 token  ", style="dim")
        line.append(spark([t["input"] + t["output"] for t in turns]), style="bold")
        avg = sum(t["input"] + t["output"] for t in turns) / len(turns)
        cost = sum(t["cost_usd"] for t in turns) / len(turns)
        line.append(f"\n最近 {len(turns)} 回合，平均每回合 {tokens(avg)} token · ${cost:.4f}", style="dim")
        parts.append(line)
    book = spend.ledger()
    cap = spend.budget()
    across = Text(f"\n本机按量计费调用累计估算： ${book.get('total_usd', 0.0):.3f}", style="dim")
    across.append(f"，预算 ${cap:g}" if cap is not None else "，未设置预算", style="dim")
    parts += [across, Text("金额为估算值；Claude Code 使用其报告的等价 API 价，不代表订阅账单。逐次记录：autokeeper trace", style="dim")]
    return Group(*parts)


class UsageModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "关闭"), Binding("ctrl+b", "dismiss", "关闭", show=False)]

    def __init__(self, state: GameState, engine: str = ""):
        super().__init__()
        self.state, self.engine = state, engine

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"用量 · 第 {self.state.turn} 轮" + (f" · {self.engine}" if self.engine else ""), classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(usage_view(self.state))
            yield Static("Esc 关闭", classes="system")
