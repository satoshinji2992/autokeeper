"""开发者模式 in the game screen itself: what this turn called (which role, which model, how long, how
much was cached), the roll that was made, where everyone really is, and what is due next. The web
page (engine/developer_web.py) keeps the full records; this is what changes on screen when the mode
is on (the user, 2026-10-21: entering it should look different)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from ...engine.state import format_time
from ..modals.usage import AGENTS

if TYPE_CHECKING:
    from ...engine.game import Game

SOON = 5  # things due, at most


def _model(name: str) -> str:
    return name.split(":", 1)[-1].removeprefix("claude-").removesuffix("-5-5")


def calls_line(rows: list[dict]) -> Text:
    """The latest turn's model calls in the order made: role, model, seconds, input and cache."""
    t = Text()
    if not rows:
        return Text("本轮还没有模型调用。", style="dim")
    turn = max(r.get("turn", 0) for r in rows)
    mine = [r for r in rows if r.get("turn", 0) == turn]
    t.append(f"第 {turn} 轮调用 ", style="bold")
    for i, r in enumerate(mine):
        agent = (r.get("session") or r.get("agent") or "").split(":")[0]
        name = AGENTS.get(agent, agent)
        tokens = int(r.get("input_tokens") or 0)
        cached = int(r.get("cache_read") or 0)
        t.append(" · " if i else "")
        t.append(name, style="bold")
        t.append(f" {_model(str(r.get('model', '')))} {float(r.get('seconds') or 0):.1f}s")
        if tokens:
            t.append(f" {tokens / 1000:.1f}k", style="dim")
            t.append(f" 缓存{100 * cached // tokens}%", style="dim" if cached * 2 >= tokens else "yellow")
        if r.get("repairs"):
            t.append(f" 修复{r['repairs']}", style="yellow")
        if r.get("error"):
            t.append(f" ✗{str(r['error']).split(':')[0]}", style="red")
    total = sum(float(r.get("seconds") or 0) for r in mine)
    t.append(f"  合计 {total:.1f}s", style="bold")
    return t


def view(game: Game, url: str = "", errors: list[str] | None = None) -> Text:
    st, m, w = game.state, game.m, game.world
    t = Text()
    t.append(" 开发者模式 ", style="bold reverse")
    t.append("  F12 关闭 · Ctrl+K 规则插件", style="dim")
    if url:
        t.append("  ")
        t.append("打开完整记录网页", style=f"underline link {url}")
    t.append("\n")
    t.append_text(calls_line(list(getattr(game.cast, "debug_calls", []))))
    t.append("\n")
    checks = [r for r in st.checks if r.turn == st.turn] or st.checks[-1:]
    if checks:
        r = checks[-1]
        t.append("检定 ", style="bold")
        t.append(f"{r.skill} · {r.text}" + (f" · 对象 {r.target}" if r.target else ""))
        t.append("\n")
    here = {n.id for n in m.npcs_at(st.location)}
    t.append("人物实际位置 ", style="bold")
    people = []
    for n in m.npcs:
        place = w.place(n.id) if w is not None else n.location
        where = w.name(place) if w is not None and place else (m.location(place).name if place else "不明")
        mark = "●" if n.id in here else "○"
        people.append(f"{mark}{n.name}@{where}" + (f"（{n.status}）" if n.status else ""))
    t.append("  ".join(people) or "无")
    t.append("\n")
    if w is not None:
        rec = w.rec
        soon = [(s.at, s.text) for s in rec.scheduled.values() if s.status == "pending" and s.at >= w.now]
        soon += [(s.due, f"{w.name(npc)}：{s.text}") for npc, a in rec.agents.items()
                 for s in a.plan if s.status == "pending" and s.due is not None and s.due >= w.now]
        t.append("接下来 ", style="bold")
        t.append("；".join(f"{format_time(at)} {text[:40]}" for at, text in sorted(soon)[:SOON]) or "无")
        t.append("\n")
    flags = "、".join(st.flags) or "无"
    t.append("标记 ", style="bold")
    t.append(flags)
    running = [p.kind for p in getattr(game, "backstage", []) if not p.task.done()]
    if running:
        t.append("  幕后进行中：" + "、".join(running), style="yellow")
    for e in (errors or [])[-2:]:
        t.append(f"\n错误 {e[:120]}", style="red")
    return t


class DevPanel(VerticalScroll):
    """Under the story while 开发者模式 is on."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.body = Static()
        self.display = False

    def compose(self):
        yield self.body

    def show(self, game: Game | None, url: str = "", errors: list[str] | None = None) -> None:
        if game is not None and self.display:
            self.put(view(game, url, errors))

    def put(self, text: Text) -> None:
        """What view() made, where it was made elsewhere (a LAN host's panel, made on the Hub's loop)."""
        if self.display:
            self.body.update(text)
