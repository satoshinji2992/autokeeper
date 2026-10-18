"""故事至今: the game so far in a paragraph, for the players (the 进展 page beside the story).

Every EVERY turns, once a turn is over, it is written again from the last one and what the
table was shown since: the keeper's narration, the investigators' actions, what people said
to them, the clues and handouts found, and the hook they started from. It is never given the
module's truth, the keeper's notes, anyone's secrets or the director's plans, so it cannot
give them away. The summarizer's cheap model writes it in the background (Game._recount): no
turn waits for it, and when a call fails the old text stands until the next one is due."""

from __future__ import annotations

from ..module.schema import Module
from .state import GameState, LogEntry

EVERY = 3  # turns between two accounts
LIMIT = 200  # characters asked for
SHOWN = ("opening", "keeper", "player", "npc", "clue", "handout", "event", "sanity", "damage", "ending")  # what the table saw
LINES = 60  # log lines one account reads at most
LONG = 400  # characters of one line
TELLER = {"opening": "开场", "keeper": "守秘人", "clue": "找到线索", "event": "发生", "ending": "结局"}


def due(st: GameState, every: int = EVERY) -> bool:
    """After a turn: the next account is due, or the game has ended since the last call."""
    p = st.progress
    if st.logged <= p.seen:
        return False
    asked = p.next - every if p.next else -1  # the turn of the last call
    return st.turn >= (p.next or every) or (bool(st.ending) and asked < st.turn)


def material(m: Module, st: GameState) -> str:
    """The prompt: the hook, the last account, and the log lines it has not read yet."""
    p = st.progress
    fresh = st.logged - p.seen
    new = st.log[-fresh:] if fresh > 0 else []
    rows = [_line(m, st, e) for e in new if e.kind in SHOWN][-LINES:]
    parts = [f"【缘起：开场时调查员就知道的事】\n{_flat(m.hook)}"]
    if p.text:
        parts.append(f"【之前写的故事至今（第 {p.turn} 轮）】\n{p.text}")
    if st.memory and (not p.text or fresh > len(st.log)):  # what the log has folded away
        parts.append(f"【更早的经过（摘要）】\n{st.memory}")
    parts.append("【这之后发生的事】\n" + ("\n".join(rows) or "（没有新的事）"))
    party = "、".join(f"{i.name}（{i.occupation}）" for i in st.investigators)
    parts.append(f"【现在】{st.clock}，{m.location(st.location).name}；调查员：{party}")
    parts.append(f"把新发生的事并进去，写一段完整的「故事至今」，不超过 {LIMIT} 字。")
    return "\n\n".join(parts)


def _line(m: Module, st: GameState, e: LogEntry) -> str:
    text = _flat(e.text)[:LONG]
    if e.kind == "handout":  # the log keeps its title; its words were on their screen
        h = next((h for h in m.handouts if h.title == e.text and h.id in st.handouts), None)
        text = f"〔{e.text}〕{_flat(h.text)[:LONG]}" if h else f"〔{e.text}〕"
        return f"[{e.time}] 拿到手稿：{text}"
    who = f"{e.who}（NPC）" if e.kind == "npc" else TELLER.get(e.kind) or e.who
    return f"[{e.time}] {who}：{text}"


def trim(text: str) -> str:
    """The account as the page shows it: plain lines, not much past the limit."""
    lines = [line.strip().replace("**", "").lstrip("#•·*- ").strip() for line in text.strip().splitlines()]
    out = "\n".join(line for line in lines if line)
    most = LIMIT * 3 // 2
    if len(out) > most:
        cut = out.rfind("。", 0, most)
        out = out[: cut + 1] if cut > LIMIT // 2 else out[:most] + "…"
    return out


def _flat(text: str) -> str:
    """Module or log text on one line (YAML wraps it)."""
    return "".join(line.strip() for line in text.strip().splitlines())
