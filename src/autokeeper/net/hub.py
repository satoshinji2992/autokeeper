"""LAN play: one game, several players.

The host runs a Hub. It owns the only copy of the game (module, state, agents) and plays
it in rounds. A round opens an action pool with one slot more than there are players at the
table (one player: two, two players: three): everyone has a slot of their own, and one of
them may take the extra one for a second action. Players send actions when they like, in any
order; an empty Enter says "that's all from me this round", /undo takes back your last one.
When the pool is full, or everyone is done, it closes and the keeper settles it in the order
the actions came in, then lets each AI seat act; then the next round opens. Nothing said
meanwhile is lost: an action sent while the keeper tells, or that the round has no room
for, waits for the next round and goes in first. Every player's screen connects over a
websocket and receives the engine's events as they happen. Seats are humans or AI; a human
who drops out is played by the AI until they sit down again. Lines in full-width brackets
are table talk and reach everyone at once.
A check asked for with .ra is that turn's action; the instant dice commands (.r, .st)
leave the turn open, and once the game is over they still work, growth rolls (.en) too.
A seated player throws their own dice: the roll prompt reaches them as an offer of kind
"roll", like a push offer, and the turn goes on when they answer (or leave). While the
table waits for a seated player, any seated player may ask the keeper to tell the latest
narration again (the turn loop does it, between actions); after the end, too. A seated
player may also ask for ideas (F5) at any time: the host's investigator agent thinks for
their seat from what the players have seen, and only they get the answer.

Messages are JSON objects with a "t" field:

    client -> hub   hello {name} · sit {investigator} · start · act {text} · choice {mode} · say {text}
                    · retell {nudge} · epilogue · suggest
    client -> hub   (in a round) done · undo
    hub -> client   welcome · seats · turn (the action being settled)
                    · pool {open, capacity, count, by, done, waiting, open_to, extra, mine {now, next}}
                    · event {ev} · module · sync {state} · offer · say · ideas {ideas}
                    · over · error

Players' screens never hold the keeper's book while the game is on: they get the module
without its truth, keeper notes, NPC minds and anything not yet found, and a state without
agent memory or the director's plans. Once it has ended the whole module goes out, so every
screen can open the debrief (复盘); the epilogue is told on the host and reaches everyone.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import __version__, config, roster
from ..engine import debrief, fog
from ..engine import graph as turn_graph
from ..engine.autoplay import situation
from ..engine.game import PLAY_WAIT, RETELL, Game, Told
from ..engine.state import GameState
from ..module.schema import Module
from ..rules import commands
from . import media

SYNC_AFTER = {"clue", "move", "time", "sanity", "damage", "flag", "sheet", "luck", "handout", "ending", "plugin_effect"}
SEATS = ("玩家", "AI", "不上场")
PER_PLAYER = 2  # actions one player may put in a round
PRIVATE = {"transcripts", "notes", "usage", "turn_usage", "tally_at", "log", "memory", "improv", "missed", "visuals", "pending_turn"}
PRIVATE |= {"plugin_data", "plugin_versions", "plugin_sources", "plugin_receipts", "plugin_errors"}


def table(m: Module, st: GameState) -> dict[str, Any]:
    """The module as a player's screen may hold it: places, names, faces, pictures, and the
    clues and handouts already found (the rest arrive with the events that reveal them)."""
    d = m.model_dump(mode="json")
    d["plugins"] = []
    d["plugin_info"] = public_plugins(m, st)
    known = fog.bearings(m, st) | {st.location: m.location(st.location).name}
    visited = set(st.visited) | {st.location}
    d.update(truth="", keeper_notes="", flags={}, creatures=[], conclusions=[], events=[], endings=[])
    d["opening"] = ""  # material not yet spoken is not part of a player's knowledge
    d["locations"] = [loc for loc in d["locations"] if loc["id"] in known]
    for loc in d["locations"]:
        loc["keeper"] = ""
        loc["exits"] = [pid for pid in loc["exits"] if pid in known]
        loc["travel_minutes"] = {pid: value for pid, value in loc["travel_minutes"].items() if pid in loc["exits"]}
        if loc["id"] not in visited:
            loc.update(name=known[loc["id"]], read_aloud="", features=[], exits=[], travel_minutes={}, art=None, weather=[])
        else:
            loc["features"] = []  # a keeper's searchable inventory is not a player's observation
    d["npcs"] = [n for n in d["npcs"] if n["location"] == st.location or (st.npcs.get(n["id"]) and st.npcs[n["id"]].met)]
    for n in d["npcs"]:
        n.update(role="", personality="", voice="", goal="", knows=[], secrets=[], lies=[], clues=[], gates={}, stats={}, arc="")
        if n["location"] not in visited:
            n["location"] = None
    for c in d["clues"]:
        if c["id"] not in st.clues:
            c.update(title="", text="", feature="", check=None, npc=None, core=False, handout=None, points_to=[], sanity=None)
            c["where"] = ""
    for h in d["handouts"]:
        if h["id"] not in st.handouts:
            h.update(title="", text="")
    d["items"] = [item for item in d["items"] if item["where"] == st.location or item["where"] in st.visited]
    for e in d["events"]:
        e.update(title="", text="", read_aloud="", performance="")
    for e in d["endings"]:
        e.update(title="", text="", reward="")
    for sk in d["sketches"]:
        sk["target"] = ""
    d["clues"] = [c for c in d["clues"] if c["id"] in st.clues]
    for c in d["clues"]:
        c.update(check=None, points_to=[], core=False, npc=None)
    d["handouts"] = [h for h in d["handouts"] if h["id"] in st.handouts]
    d["sketches"] = [sk for sk in d["sketches"] if not sk["after"] or sk["after"] in st.clues]
    return d


def public_plugins(m: Module, st: GameState) -> list[dict[str, Any]]:
    """The active rule plugins as a player's screen may hold them: the name, version, plain
    description and the custom actions with their descriptions. No id, source, counters or error
    text — those stay on the host."""
    out = []
    for identity in m.plugins:
        plugin = m.extensions.get(identity)
        if plugin is None:
            continue
        out.append({
            "name": plugin.name, "version": plugin.version, "description": plugin.description,
            "actions": [[a.name, a.description] for a in plugin.actions],
            "paused": identity in st.plugin_errors,
        })
    return out


def room_key_file(port: int):
    """Where a running room keeps its host key: only someone on this machine can read it."""
    return config.SAVES / f".room-{port}.key"


def room_key(url: str) -> str:
    """The host key, for a `join` from the host's own machine (not the browser seats, which the
    host command starts with AUTOKEEPER_GUEST set)."""
    from urllib.parse import urlparse

    target = urlparse(url)
    if os.environ.get("AUTOKEEPER_GUEST") or target.hostname not in ("127.0.0.1", "localhost", "::1"):
        return ""
    try:
        return room_key_file(target.port or 8765).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Hub:
    def __init__(
        self,
        module_dir: Path,
        *,
        seats: list[str] | None = None,
        slot: str | None = None,
        state: GameState | None = None,
        cast: Any = None,
        rules: Any = None,
    ):
        from ..agents.cast import Cast
        from ..rules.coc import Rules

        self.dir = Path(module_dir)
        self.m = Module.load(self.dir)
        self.fresh = state is None
        if state is None:
            plan = [("AI" if s.lower() == "ai" else s) for s in seats] if seats else ["玩家"] + ["AI"] * (len(self.m.pregens) - 1)
            if odd := [s for s in plan if s not in SEATS]:
                raise ValueError(f"座位只能填 {' / '.join(SEATS)}，看不懂：{'、'.join(odd)}")
            if len(plan) > len(self.m.pregens):
                raise ValueError(f"这个模组只有 {len(self.m.pregens)} 位预设调查员，座位填多了")
            chosen = [(p, s) for p, s in zip(self.m.pregens, plan, strict=False) if s != "不上场"]
            if not chosen:
                raise ValueError("至少要有一位调查员上场")
            party = [p.model_copy(deep=True) for p, _ in chosen]
            human = [p.id for p, s in chosen if s == "玩家"]
            state = Game.new(self.m, party, slot=slot or f"lan-{self.m.id}-{time.strftime('%m%d-%H%M%S')}", human=human)
        self.state = state
        roster.attach(self.m, state.investigators)
        self.cast = cast or Cast(config.load(), self.m)
        self.game = Game(self.m, state, self.cast, rules or Rules(), emit=self._event)
        self.game.director_wait = PLAY_WAIT
        self.graph = turn_graph.build(self.game, InMemorySaver())
        self.game.checkpoint = self._save
        self.thread = {"configurable": {"thread_id": state.slot}}
        self.clients: dict[str, web.WebSocketResponse] = {}
        self.names: dict[str, str] = {}
        self.sits: dict[str, str | None] = {inv_id: None for inv_id in state.human}  # human seat -> client
        self.inbox: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in state.human}
        self.choices: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in state.human}
        self.started = asyncio.Event()
        self.open = False  # the round's action pool takes actions
        self.pool: list[tuple[str, str]] = []  # (seat, action) in the order they came in
        self.done: set[str] = set()  # seats that said they have nothing more this round
        self.early: list[tuple[str, str]] = []  # (seat, action) for the next round: sent while the keeper told, or with no room
        self.instant: asyncio.Queue = asyncio.Queue()  # (seat, .r / .st ...) to run while the pool is open
        self.wake = asyncio.Event()  # something for the round loop: an action, a request, someone left
        self.retrying: str | None = None  # the seat whose settled action broke off, waiting for its /retry
        self.over = False  # the game has ended and its last turn is told
        self.desk = asyncio.Lock()  # after the end, one player's command at a time
        self.asks: asyncio.Queue = asyncio.Queue()  # retellings asked for mid-game: (client, nudge, narration), done between actions
        self.thinking: dict[str, asyncio.Task] = {}  # client -> the ideas being thought of for them (F5)
        self.port = 0
        self.key = secrets.token_hex(16)  # room_key(): the host's own `join` proves it sits at this machine
        self.hosts: set[str] = set()  # clients that showed the key: the host may switch models
        self.swaps: asyncio.Queue = asyncio.Queue()  # model switches asked for, done between actions
        self.rules: asyncio.Queue = asyncio.Queue()  # house rules asked for, drafted and installed between actions

    # -- connections --------------------------------------------------------------------
    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        cid = uuid.uuid4().hex[:8]
        self.clients[cid] = ws
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self._on(cid, json.loads(msg.data))
                    except (KeyError, ValueError, TypeError) as exc:
                        await self._send(cid, {"t": "error", "text": f"看不懂的消息：{exc}"})
        finally:
            self.clients.pop(cid, None)
            for inv_id, who in self.sits.items():
                if who == cid:  # whoever waits for them gets an empty answer: the AI plays on
                    self.sits[inv_id] = None
                    self.early = [(seat, text) for seat, text in self.early if seat != inv_id]
                    self.inbox[inv_id].put_nowait(None)
                    self.choices[inv_id].put_nowait(None)
                    self.wake.set()
            await self._broadcast({"t": "seats", "seats": self._seats()})
        return ws

    async def _on(self, cid: str, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "hello":
            self.names[cid] = (msg.get("name") or "").strip()[:16] or f"玩家{len(self.names) + 1}"
            if msg.get("key") and secrets.compare_digest(str(msg["key"]), self.key):
                self.hosts.add(cid)
                self.names[cid] += "·房主"
            await self._send(cid, self._welcome(cid))
            await self._broadcast({"t": "seats", "seats": self._seats()})
        elif t == "sit":
            await self._sit(cid, msg.get("investigator", ""))
        elif t == "start":
            self.started.set()
        elif t == "act":
            seat = self._seat_of(cid)
            text = str(msg.get("text", ""))[:500]
            if seat is None:
                await self._send(cid, {"t": "error", "text": "你在观战，没有自己的调查员"})
            elif self.over and commands.instant(text):  # growth rolls (.en) and the sheet still work
                await self._afterwards(seat, text)
            elif self.over:
                await self._send(cid, {"t": "error", "text": "这局已经结束了"})
            elif self.retrying == seat and text.strip().startswith("/retry"):
                self.inbox[seat].put_nowait(text)
            elif text.strip().startswith("/"):
                await self._send(cid, {"t": "error", "text": "现在没有要接着讲的回合"})
            elif commands.instant(text):  # .r, .st: not the round's action; run while the pool is open
                self.instant.put_nowait((seat, text))
                self.wake.set()
            elif not text.strip():
                return
            elif self.open and self._may_add(seat):
                self.pool.append((seat, text))
                self.done.discard(seat)
                self.wake.set()
                await self._pool()
            elif sum(1 for s, _ in self.early if s == seat) >= PER_PLAYER:
                await self._send(cid, {"t": "error", "text": f"下一轮你已经先说了 {PER_PLAYER} 条；/undo 撤回上一条"})
            else:  # the keeper is telling, or this round has no room for it: first in the next round
                self.early.append((seat, text))
                await self._pool()
        elif t == "done":
            seat = self._seat_of(cid)
            if seat is not None and self.open:
                self.done.add(seat)
                self.wake.set()
                await self._pool()
        elif t == "undo":  # your latest line: one waiting for the next round, else one in this round
            seat = self._seat_of(cid)
            if seat is None:
                return
            held = [i for i, (s, _) in enumerate(self.early) if s == seat]
            mine = [i for i, (s, _) in enumerate(self.pool) if s == seat] if self.open else []
            if held:
                self.early.pop(held[-1])
            elif mine:
                self.pool.pop(mine[-1])
                self.done.discard(seat)
            else:
                return
            self.wake.set()
            await self._pool()
        elif t == "choice":
            seat = self._seat_of(cid)
            if seat is not None:
                self.choices[seat].put_nowait(msg.get("mode"))
        elif t == "say":
            await self._broadcast({"t": "say", "from": self.names.get(cid, "?"), "text": str(msg.get("text", ""))[:500]})
        elif t == "retell":
            await self._ask_retell(cid, str(msg.get("nudge") or "换个讲法"))
        elif t == "epilogue":
            if not self.over or not self.state.ending:
                await self._send(cid, {"t": "error", "text": "后日谈要等这一局结束"})
            else:
                async with self.desk:
                    await self._afterword()
        elif t == "engine":
            if cid not in self.hosts:
                await self._send(cid, {"t": "error", "text": "只有房主能换模型"})
            elif isinstance(msg.get("choice"), dict):
                self.swaps.put_nowait((cid, msg["choice"]))
                await self._send(cid, {"t": "error", "text": "这一段讲完就换模型"})
        elif t == "suggest":
            seat = self._seat_of(cid)
            if seat is None or self.state.ending:
                await self._send(cid, {"t": "ideas", "ideas": []})
            elif cid not in self.thinking:  # one at a time: each costs the host a call
                self.thinking[cid] = asyncio.create_task(self._ideas(cid, seat))
        elif t == "house_rule":  # the rule's code runs on the host's machine: only the host asks for one
            text = str(msg.get("text", "")).strip()[:500]
            if cid not in self.hosts:
                await self._send(cid, {"t": "error", "text": "家规只能由房主添加，跟房主说一声"})
            elif text:
                self.rules.put_nowait((cid, text))
                self.wake.set()
                await self._send(cid, {"t": "error", "text": "这一段讲完就写这条规则"})

    async def _ideas(self, cid: str, seat: str) -> None:
        """Three ideas for a seated player's next move, from the view the AI seats get. The game
        does not wait for them, and they go to that player alone."""
        try:
            ideas = await self.cast.suggest(self.state, seat, situation(self.m, self.state))
        except Exception:  # the table plays on without them; the player's screen says none came
            ideas = []
        finally:
            self.thinking.pop(cid, None)
        await self._send(cid, {"t": "ideas", "ideas": ideas})

    async def _sit(self, cid: str, inv_id: str) -> None:
        if inv_id not in self.sits:
            await self._send(cid, {"t": "error", "text": "这个座位由 AI 扮演，坐不了"})
            return
        if self.sits[inv_id] not in (None, cid):
            await self._send(cid, {"t": "error", "text": "这个座位已经有人了"})
            return
        for seat, who in self.sits.items():
            if who == cid and seat != inv_id:
                self.sits[seat] = None
        self.sits[inv_id] = cid
        await self._broadcast({"t": "seats", "seats": self._seats()})
        if all(self.sits.values()):  # every human seat is taken: the game begins
            self.started.set()

    def _seat_of(self, cid: str) -> str | None:
        return next((seat for seat, who in self.sits.items() if who == cid), None)

    # -- what clients see ------------------------------------------------------------------
    def _view(self) -> dict[str, Any]:
        view = self.state.model_dump(mode="json", exclude=PRIVATE)
        view["flags"], view["fired"] = [], []
        if view.get("pending_push"):
            view["pending_push"] = {k: v for k, v in view["pending_push"].items() if k not in ("clues", "flags")}
        return view

    def _seats(self) -> list[dict[str, Any]]:
        out = []
        for inv in self.state.investigators:
            who = self.sits.get(inv.id)
            out.append({
                "investigator": inv.id, "name": inv.name, "occupation": inv.occupation,
                "human": inv.id in self.sits, "player": self.names.get(who or "", ""), "online": who is not None,
            })  # fmt: skip
        return out

    def _welcome(self, cid: str) -> dict[str, Any]:
        book = self._book()
        keys = self._art_keys(book)
        return {
            "t": "welcome",
            "you": cid,
            "host": cid in self.hosts,
            "version": __version__,  # a client on another version is told so (tui/net.py)
            "module": book,
            "art": {k: v for k, v in self.m.art.items() if k in keys},
            "colors": {k: v for k, v in self.m.art_colors.items() if k in keys},
            "picture_data": self._pictures(book, keys),
            "state": self._view(),
            "log": [e.model_dump(mode="json") for e in self.state.log[-40:]],
            "seats": self._seats(),
            "started": self.started.is_set(),
        }

    def _art_keys(self, book: dict) -> set[str]:
        return ({x.get("art") for x in book["locations"] + book["items"]}
                | {x.get("portrait") for x in book["npcs"]} | {i.portrait for i in self.state.investigators}
                | {key for h in book["handouts"] for key in h.get("images", [])})

    def _pictures(self, book: dict, keys: set[str]) -> dict[str, str]:
        handouts = {key for h in book["handouts"] for key in h.get("images", [])}
        return {k: media.pack(v, max_size=1600 if k in handouts else 512)
                for k, v in self.m.pictures.items() if k in keys}

    async def _share_book(self) -> None:
        book = self._book()
        keys = self._art_keys(book)
        await self._broadcast({"t": "module", "module": book,
                               "art": {k: v for k, v in self.m.art.items() if k in keys},
                               "colors": {k: v for k, v in self.m.art_colors.items() if k in keys}})

    def _book(self) -> dict[str, Any]:
        """The module for the players' screens: the table's copy while the game is on, the
        whole book once it has ended (the debrief lays it open anyway)."""
        return self.m.model_dump(mode="json") if self.state.ending else table(self.m, self.state)

    async def _send(self, cid: str, msg: dict[str, Any]) -> None:
        ws = self.clients.get(cid)
        if ws is not None and not ws.closed:
            try:
                await ws.send_str(json.dumps(msg, ensure_ascii=False, default=str))
            except ConnectionError:  # gone mid-send; the connection handler cleans up
                pass

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        for cid in list(self.clients):
            await self._send(cid, msg)

    async def _event(self, ev: dict[str, Any]) -> None:
        if ev.get("type") in ("ruling", "plugin_status"):
            return  # the arbiter's private explanation and unrevealed clue ids stay on the host
        if ev.get("type") == "flag":
            await self._broadcast({"t": "sync", "state": self._view()})
            return
        if ev.get("type") in ("improv", "move", "npc", "performance", "clue", "handout"):
            await self._share_book()
            ev = {k: v for k, v in ev.items() if k != "reason"}  # the director's reasoning is for the keeper
            if ev.get("type") == "handout":
                book = self._book()
                handout = next((h for h in book["handouts"] if h["id"] == ev.get("id")), None)
                if handout:
                    ev["picture_data"] = self._pictures(book, set(handout.get("images", [])))
        elif ev.get("type") == "art":
            if ev["key"] not in self._art_keys(self._book()):
                return
            ev = {k: v for k, v in ev.items() if k in ("type", "key", "art", "mask", "status")}
            path = self.m.pictures.get(ev["key"])
            if path:
                ev["picture_data"] = media.pack(path)
        elif ev.get("type") == "ending":  # the book opens with the ending, before any screen knows it is over
            await self._broadcast({"t": "module", "module": self._book()})
        elif ev.get("type") == "plugin_effect":  # public line in the story; the id stays on the host
            ev = {k: v for k, v in ev.items() if k != "plugin"}
        await self._broadcast({"t": "event", "ev": ev})
        if ev.get("type") in SYNC_AFTER:
            await self._broadcast({"t": "sync", "state": self._view()})

    # -- the game ---------------------------------------------------------------------------
    async def play(self) -> None:
        await self.started.wait()
        await self._broadcast({"t": "begin"})
        st = self.state
        if self.fresh and not st.log:
            await self.game.start()
            self._save()
            await self._broadcast({"t": "sync", "state": self._view()})
        while not st.ending:
            if not any(inv.can_act for inv in st.investigators):  # nobody left standing
                break
            await self._collect()
            await self._settle()
        self.over = True
        await self._broadcast({"t": "over", "ending": st.ending, "state": self._view()})

    def _count(self, seat: str) -> int:
        return sum(1 for s, _ in self.pool if s == seat)

    def _players(self) -> list[str]:
        """Seated players still at the table and able to act."""
        return [inv.id for inv in self.state.investigators if inv.can_act and self.sits.get(inv.id) is not None]

    def capacity(self) -> int:
        """One slot more than there are players: someone can do two things, nobody three."""
        players = len(self._players())
        return players + 1 if players else 0

    def _extra(self) -> str | None:
        """The seat that took the round's extra slot with a second action, if one has."""
        return next((seat for seat, _ in self.pool if self._count(seat) >= PER_PLAYER), None)

    def _may_add(self, seat: str) -> bool:
        """Room in this round for one more of the seat's actions: its own slot if it has not
        used it, else the extra one while nobody has it. A second action never takes the
        place of somebody's first."""
        n = self._count(seat)
        return len(self.pool) < self.capacity() and (n == 0 or (n < PER_PLAYER and self._extra() is None))

    def _waiting_for(self) -> list[str]:
        """Players who may still put an action in: not done, and with room for it."""
        return [seat for seat in self._players() if seat not in self.done and self._may_add(seat)]

    async def _pool(self) -> None:
        """The round as everyone sees it, and each player's own lines: in it, and waiting for the next."""
        names = {inv.id: inv.name for inv in self.state.investigators}
        waiting = self._waiting_for() if self.open else []
        public = {"t": "pool", "open": self.open, "capacity": self.capacity(), "count": len(self.pool),
                  "by": {seat: self._count(seat) for seat in self._players()}, "done": sorted(self.done),
                  "waiting": [names[i] for i in waiting], "open_to": waiting, "extra": names.get(self._extra() or "", "")}
        for cid in list(self.clients):
            seat = self._seat_of(cid)
            mine = {"now": [text for s, text in self.pool if s == seat], "next": [text for s, text in self.early if s == seat]}
            await self._send(cid, {**public, "mine": mine if seat else {}})

    async def _collect(self) -> None:
        """Open the pool and wait until it is full or everyone is done. What was said for this
        round while the keeper told the last goes in first. Meanwhile the table's requests
        (a retelling, the host's model switch, .r and .st) are done, and a finished plan of the
        director's plays out."""
        self.pool, self.done, self.open = [], set(), True
        for queue in self.inbox.values():
            while not queue.empty():
                queue.get_nowait()
        early, self.early = self.early, []
        players = self._players()
        for seat, text in early:  # in the order it was said; what finds no room waits a round more
            if seat in players:
                (self.pool if self._may_add(seat) else self.early).append((seat, text))
        await self._event({"type": "status", "text": ""})
        await self._pool()
        try:
            while True:  # once at least: a .r sent while the keeper told runs even if the round is already full
                await self._swap()
                await self._house_rules()
                await self._asks()
                while not self.instant.empty():
                    seat, text = self.instant.get_nowait()
                    await self._afterwards(seat, text)
                    await self._pool()
                if not self._waiting_for():
                    break
                try:
                    await asyncio.wait_for(self.wake.wait(), 1.0)
                except TimeoutError:  # while they think, a finished plan of the director's plays out
                    await self._interlude()
                self.wake.clear()
        finally:
            self.open = False
        await self._pool()

    async def _settle(self) -> None:
        """The pooled actions in the order they came in, then one for each seat nobody is
        playing (the AI's, and a player's who left without putting anything in)."""
        st = self.state
        acted = {seat for seat, _ in self.pool}
        order = [*self.pool, *[(inv.id, None) for inv in st.investigators
                               if inv.id not in acted and inv.id not in self.done and self.sits.get(inv.id) is None]]
        for n, (seat, text) in enumerate(order, 1):
            inv = next((i for i in st.investigators if i.id == seat), None)
            if st.ending:
                return
            if inv is None or not inv.can_act:
                continue
            st.active = st.investigators.index(inv)
            who = self.sits.get(seat)
            await self._broadcast({
                "t": "turn", "investigator": seat, "name": inv.name, "player": self.names.get(who or "", ""),
                "ai": text is None, "index": n, "total": len(order), "state": self._view(),
            })  # fmt: skip
            if text is None:
                await self._ai_turn(inv)
                continue
            await self._event({"type": "said", "who": inv.name, "player": self.names.get(who or "", ""), "text": text})
            while True:
                try:
                    await self._run(text, seat, ai=False)
                    break
                except Exception as exc:
                    self.game.visuals.resume()
                    await self._event({"type": "status", "text": ""})  # nobody's screen keeps spinning
                    who = self.sits.get(seat)
                    if who is None:  # they left meanwhile: on without them
                        break
                    await self._broadcast({"t": "say", "from": "系统", "text": f"{inv.name}的行动中断了（{type(exc).__name__}），等{self.names.get(who, '他')}输入 /retry 接着讲"})
                    await self._send(who, {"t": "error", "retry": True, "text": f"这一回合暂未完成（{type(exc).__name__}），输入 /retry 接着讲，已结算的结果会保留"})
                    self.retrying = seat
                    try:
                        text = await self.inbox[seat].get()
                    finally:
                        self.retrying = None
                    if text is None:
                        break

    async def _afterwards(self, seat: str, text: str) -> None:
        """After the end a player can still read their sheet and make their growth rolls."""
        async with self.desk:  # each command runs as its own investigator
            st = self.state
            st.active = next(i for i, inv in enumerate(st.investigators) if inv.id == seat)
            await self._event({"type": "said", "who": st.investigator.name, "player": self.names.get(self.sits.get(seat) or "", ""), "text": text})
            await self._run(text, seat, ai=False)

    # -- telling again, and after the end ------------------------------------------------------
    async def _ask_retell(self, cid: str, nudge: str) -> None:
        """A seated player asks for the latest narration again. Mid-game it waits for the turn
        loop, between actions, so it never runs beside a turn or an interlude; and it is that
        narration, the one on their screen when they asked, or none."""
        why = self.game.retell_blocked() or ("" if nudge in RETELL else f"可以这样要：{'、'.join(RETELL)}")
        if self._seat_of(cid) is None:
            await self._send(cid, {"t": "error", "text": "你在观战，不能请守秘人重讲"})
        elif why:
            await self._send(cid, {"t": "error", "text": why})
        elif self.over:
            async with self.desk:
                await self._tell_again(cid, nudge, self.game.told)
        elif not self.open:
            await self._send(cid, {"t": "error", "text": "等这一轮讲完再请守秘人重讲"})
        else:
            self.asks.put_nowait((cid, nudge, self.game.told))
            self.wake.set()  # the round loop does it, between actions

    async def _swap(self) -> None:
        """New channels for every agent, as GameScreen._apply_engine does; the transcripts stay
        in the state, so nobody forgets."""
        from ..agents.cast import Cast

        while not self.swaps.empty():
            cid, choice = self.swaps.get_nowait()
            os.environ["AUTOKEEPER_PRESET"] = str(choice.get("preset", ""))
            os.environ["AUTOKEEPER_MODE"] = str(choice.get("mode") or "标准")
            for key, env in (("model", "AUTOKEEPER_MODEL"), ("fallback", "AUTOKEEPER_FALLBACK")):
                if choice.get(key):
                    os.environ[env] = str(choice[key])
                else:
                    os.environ.pop(env, None)
            try:
                cfg = config.load()
            except ValueError as exc:
                await self._send(cid, {"t": "error", "text": str(exc)})
                continue
            config.save_ui_settings(choice)
            old, self.cast = self.cast, Cast(cfg, self.m)
            await self.game.replace_cast(self.cast)
            await old.close()
            keeper = cfg.role("keeper").model.split(":", 1)[-1]
            await self._broadcast({"t": "say", "from": "系统", "text": f"房主换了模型：{cfg.preset} · 守秘人 {keeper}"})

    async def _house_rules(self) -> None:
        """Draft and install the house rules players asked for, one at a time, between actions.
        The author runs on the host; the whole table hears the progress and the outcome, and the
        new rule reaches every screen through the shared book and the synced state."""
        from ..plugins.author import house_rule

        while not self.rules.empty():
            cid, text = self.rules.get_nowait()
            who = self.names.get(cid, "有人")
            await self._broadcast({"t": "say", "from": "系统", "text": f"{who}想加一条规则：{text}"})

            async def step(message: str) -> None:
                await self._broadcast({"t": "say", "from": "系统", "text": message})

            try:
                ok, message = await house_rule(self.cast, self.m, self.state, text, on_step=step)
            except Exception as exc:  # the table plays on; the rule just did not land
                ok, message = False, f"没能添加（{exc.__class__.__name__}）"
            await self._broadcast({"t": "say", "from": "系统", "text": message})
            if ok:
                self._save()
                await self._share_book()
                await self._broadcast({"t": "sync", "state": self._view()})

    async def _asks(self) -> None:
        while not self.asks.empty():
            await self._tell_again(*self.asks.get_nowait())

    async def _tell_again(self, cid: str, nudge: str, told: Told | None) -> None:
        why = "守秘人已经讲到下一段了，想重讲就再要一次" if self.game.told is not told else self.game.retell_blocked()
        if why:
            await self._send(cid, {"t": "error", "text": why})
            return
        await self._event({"type": "retold", "who": self.names.get(cid, ""), "nudge": nudge})
        self.game.emit = self._event
        try:
            await self.game.retell(nudge)
        except Exception as exc:  # the old telling is back on every screen
            await self._send(cid, {"t": "error", "text": f"守秘人没能重讲（{exc.__class__.__name__}），原来那段还在"})
            return
        self._save()
        await self._broadcast({"t": "sync", "state": self._view()})

    async def _afterword(self) -> None:
        """The epilogue, told once on the host for the whole table; asked again, the kept one."""
        self.game.emit = self._event
        try:
            await debrief.epilogue(self.game)
        except Exception:  # the screens heard about it through the epilogue event
            return
        self._save()
        await self._broadcast({"t": "sync", "state": self._view()})

    async def _interlude(self) -> None:
        if self.game.ready():
            self.game.emit = self._event
            await self.game.interlude()
            self._save()
            await self._broadcast({"t": "sync", "state": self._view()})

    async def _ai_turn(self, inv: Any) -> None:
        await self._interlude()
        await self._event({"type": "status", "text": f"{inv.name}在想下一步…"})
        text = await self.cast.investigator(self.state, inv.id, situation(self.m, self.state))
        await self._event({"type": "said", "who": inv.name, "player": "", "text": text, "ai": True})
        await self._run(text, inv.id, ai=True)

    async def _run(self, text: str, inv_id: str, *, ai: bool) -> None:
        inp: Any = {"input": text, "by_hand": not ai and self.sits.get(inv_id) is not None}  # a seated player throws their own dice
        while True:
            offer = None
            async for mode, chunk in self.graph.astream(inp, self.thread, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    await self._event(chunk)
                elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                    offer = chunk["__interrupt__"][0].value
            if offer is None:
                break
            choice = None
            who = self.sits.get(inv_id)
            if not ai and who is not None:
                queue = self.choices[inv_id]
                while not queue.empty():
                    queue.get_nowait()
                await self._send(who, {"t": "offer", "offer": offer})
                choice = await queue.get()
            if offer.get("kind") == "roll":  # thrown, or they left: either way the dice fall
                choice = "roll"
            elif choice not in ("push", "luck", "drop"):
                choice = "push" if offer.get("can_push") else "luck" if offer.get("luck") is not None else "drop"
            inp = Command(resume=choice)
        self.game.emit = self._event
        self._save()
        await self._broadcast({"t": "sync", "state": self._view()})

    def _save(self) -> None:
        from ..tui.app import write_save

        write_save(self.state, self.m)

    # -- serving ----------------------------------------------------------------------------
    async def serve(self, host: str = "0.0.0.0", port: int = 8765, *, linger: bool = True) -> None:
        """Listen, play the game, and keep the room open afterwards so everyone sees the end."""
        app = web.Application()
        app.router.add_get("/ws", self.ws)
        app.router.add_get("/", self._status)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1] if site._server else port  # type: ignore[union-attr]
        key_file = room_key_file(self.port)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(self.key, encoding="utf-8")
        try:
            await self.play()
            if linger:
                await asyncio.Event().wait()
        finally:
            key_file.unlink(missing_ok=True)
            await self.game.close()
            await self.cast.close()
            await runner.cleanup()

    async def _status(self, request: web.Request) -> web.Response:
        body = {"module": self.m.title, "seats": self._seats(), "started": self.started.is_set(), "clock": self.state.clock}
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))
