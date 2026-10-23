"""故事至今: the game so far in a paragraph, for the players (the 进展 page beside the story).

It is written again only when the story has moved since the last one: a milestone (a clue or a
handout found, a place reached or a person met for the first time, a plot beat — an event, a
story flag, the director's plan landing —, a notable loss of sanity or HP, a jump in time or a
new day), and never sooner than APART turns after the last call. Talk, narration and looking
around alone never bring one; after STALE turns of them one comes anyway, so the page is not
stale for ever; with nothing new at all, never. The ending always gets a last one at once, and
a player may ask for one (Ctrl+Y), once a turn.

It is written from the last one and what the table was shown since: the keeper's narration,
the investigators' actions, what people said to them, the handouts found, and the hook they
started from. It is never given the module's truth, the keeper's notes, anyone's secrets or the
director's plans, so it cannot give them away; nor the module's own words for a clue found (the
user, 2026-10-22: the players hear it in the story and keep their own notes), only the telling
that put it into the story. The summarizer's cheap model
writes it in the background (Game._recount): no turn waits for it, and when a call fails the
old text stands."""

from __future__ import annotations

from ..module.schema import Module
from . import audience
from .state import GameState, LogEntry

APART = 6  # turns at least between two calls, milestones or not; the ending excepted
STALE = 15  # turns of play without a milestone after which one is written anyway
SAN_LOSS = 3  # the party's sanity lost since the last account that is a milestone
HP_LOSS = 1  # HP lost since then that is one: any wound
TIME_SKIP = 240  # game minutes gone since then that count as a jump in time (a rest, a night)
LIMIT = 200  # characters asked for
SHOWN = ("opening", "keeper", "player", "speech", "npc", "handout", "event", "sanity", "damage", "ending")  # what the table saw
LINES = 60  # log lines one account reads at most
LONG = 400  # characters of one line
TELLER = {"opening": "开场", "keeper": "守秘人", "event": "发生", "ending": "结局"}
FOUND = ("clues", "handouts", "places", "people", "beats", "worse")  # counts that only go up
WHAT = {"clues": "线索", "handouts": "手稿", "places": "新地点", "people": "新面孔", "beats": "剧情",
        "worse": "伤病或疯狂", "san": "理智", "hp": "伤势", "time": "时间", "day": "新的一天"}


def marks(st: GameState) -> dict[str, int]:
    """What has turned up so far, counted; kept with each call to tell what is new since."""
    return {
        "clues": len(st.clues), "handouts": len(st.handouts), "places": len(st.visited),
        "people": sum(n.met for n in st.npcs.values()),
        "beats": len(st.fired) + len(st.flags) + len(st.improv),
        "worse": sum(len(i.conditions) for i in st.investigators),
        "san": sum(i.san for i in st.investigators), "hp": sum(i.hp for i in st.investigators),
        "minutes": st.minutes,
    }  # fmt: skip


def moved(st: GameState) -> list[str]:
    """The milestones since the last call (since the opening, before the first). A save from
    before these were kept counts from nothing: whatever it has found is new."""
    then, now = st.progress.marks or {"places": 1}, marks(st)
    out = [k for k in FOUND if now[k] > then.get(k, 0)]
    out += [k for k, least in (("san", SAN_LOSS), ("hp", HP_LOSS)) if k in then and then[k] - now[k] >= least]
    if "minutes" in then:
        if now["minutes"] // 1440 > then["minutes"] // 1440:
            out.append("day")
        elif now["minutes"] - then["minutes"] >= TIME_SKIP:
            out.append("time")
    return [WHAT[k] for k in out]


def due(st: GameState) -> bool:
    """After a turn: the story has moved and it is long enough since the last call, or it has
    gone quiet for long, or the game has just ended."""
    p = st.progress
    if st.logged <= p.seen:  # nothing new at all
        return False
    if st.ending:
        return p.asked < st.turn
    since = st.turn - max(p.asked, 0)
    return since >= APART and (since >= STALE or bool(moved(st)))


def refused(st: GameState) -> str:
    """Why a player's own request for one cannot be done now; "" when it can."""
    if st.progress.asked == st.turn:
        return "本回合已整理进展，可在下一回合再次整理。"
    if st.logged <= st.progress.seen:
        return "上次整理后暂无新事件。"
    return ""


def material(m: Module, st: GameState) -> str:
    """The prompt: the hook, the last account, and the log lines it has not read yet."""
    p = st.progress
    fresh = st.logged - p.seen
    new = st.log[-fresh:] if fresh > 0 else []
    rows = [_line(m, st, e) for e in new if e.kind in SHOWN and audience.shared(e)][-LINES:]
    parts = [f"【缘起：开场时调查员就知道的事】\n{_flat(m.hook)}"]
    if p.text:
        parts.append(f"【之前写的故事至今（第 {p.turn} 轮）】\n{p.text}")
    if st.memory and (not p.text or fresh > len(st.log)):  # what the log has folded away
        parts.append(f"【更早的经过（摘要）】\n{st.memory}")
    parts.append("【这之后发生的事】\n" + ("\n".join(rows) or "（没有新的事）"))
    differing = {target for masks in st.perceptions.values() for target in masks}
    party = "、".join(f"{i.name}（{i.occupation}）" for i in st.investigators if i.id not in differing) or "调查员们"
    places = {audience.location(st, investigator.id) for investigator in st.investigators}
    where = m.location(st.location).name if len(places) == 1 else "调查员正在分头行动"
    parts.append(f"【现在】{st.clock}，{where}；调查员：{party}")
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
