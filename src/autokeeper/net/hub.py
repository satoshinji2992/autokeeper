"""LAN play: one game, several players.

The host runs a Hub. It owns the only copy of the game (module, state, agents) and plays
the turns in order, one seat at a time. Every player's screen connects over a websocket,
receives the engine's events as they happen, and sends its own action when its turn
comes. Seats are humans or AI; a human who drops out is played by the AI until they
sit down again. Lines in full-width brackets are table talk and reach everyone at once.

Messages are JSON objects with a "t" field:

    client -> hub   hello {name} · sit {investigator} · start · act {text} · choice {mode} · say {text}
    hub -> client   welcome · seats · turn · event {ev} · module · sync {state} · offer · say · over · error

Players' screens never hold the keeper's book: they get the module without its truth,
keeper notes, NPC minds and anything not yet found, and a state without agent memory
or the director's plans.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import config
from ..engine import graph as turn_graph
from ..engine.autoplay import situation
from ..engine.game import Game
from ..engine.state import GameState
from ..module.schema import Module

SYNC_AFTER = {"clue", "move", "time", "sanity", "damage", "flag", "sheet", "luck", "handout", "ending"}
SEATS = ("玩家", "AI", "不上场")
PRIVATE = {"transcripts", "notes", "usage", "log", "memory", "improv"}  # agent memory, bookkeeping and the director's plans


def table(m: Module, st: GameState) -> dict[str, Any]:
    """The module as a player's screen may hold it: places, names, faces, pictures, and the
    clues and handouts already found (the rest arrive with the events that reveal them)."""
    d = m.model_dump(mode="json")
    d.update(truth="", keeper_notes="", flags=dict.fromkeys(d["flags"], ""), creatures=[])
    for k in d["conclusions"]:
        k["text"] = ""
    for loc in d["locations"]:
        loc["keeper"] = ""
    for n in d["npcs"]:
        n.update(personality="", voice="", goal="", knows=[], secrets=[], lies=[], clues=[], gates={}, stats={}, arc="")
    for c in d["clues"]:
        if c["id"] not in st.clues:
            c.update(title="", text="", feature="", check=None, npc=None, core=False, handout=None, points_to=[], sanity=None)
    for h in d["handouts"]:
        if h["id"] not in st.handouts:
            h.update(title="", text="")
    for e in d["events"]:
        e.update(title="", text="", read_aloud="", performance="")
    for e in d["endings"]:
        e.update(title="", text="", reward="")
    for sk in d["sketches"]:
        sk["target"] = ""
    return d


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
        self.cast = cast or Cast(config.load(), self.m)
        self.game = Game(self.m, state, self.cast, rules or Rules(), emit=self._event)
        self.graph = turn_graph.build(self.game, InMemorySaver())
        self.thread = {"configurable": {"thread_id": state.slot}}
        self.clients: dict[str, web.WebSocketResponse] = {}
        self.names: dict[str, str] = {}
        self.sits: dict[str, str | None] = {inv_id: None for inv_id in state.human}  # human seat -> client
        self.inbox: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in state.human}
        self.choices: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in state.human}
        self.started = asyncio.Event()
        self.waiting: str | None = None  # the seat whose action the game waits for
        self.port = 0

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
                    self.inbox[inv_id].put_nowait(None)
                    self.choices[inv_id].put_nowait(None)
            await self._broadcast({"t": "seats", "seats": self._seats()})
        return ws

    async def _on(self, cid: str, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "hello":
            self.names[cid] = (msg.get("name") or "").strip()[:16] or f"玩家{len(self.names) + 1}"
            await self._send(cid, self._welcome(cid))
            await self._broadcast({"t": "seats", "seats": self._seats()})
        elif t == "sit":
            await self._sit(cid, msg.get("investigator", ""))
        elif t == "start":
            self.started.set()
        elif t == "act":
            seat = self._seat_of(cid)
            if seat is None:
                await self._send(cid, {"t": "error", "text": "你在观战，没有自己的调查员"})
            elif self.waiting != seat:
                await self._send(cid, {"t": "error", "text": "还没轮到你"})
            else:
                self.inbox[seat].put_nowait(str(msg.get("text", ""))[:500])
        elif t == "choice":
            seat = self._seat_of(cid)
            if seat is not None:
                self.choices[seat].put_nowait(msg.get("mode"))
        elif t == "say":
            await self._broadcast({"t": "say", "from": self.names.get(cid, "?"), "text": str(msg.get("text", ""))[:500]})

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
        return self.state.model_dump(mode="json", exclude=PRIVATE)

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
        return {
            "t": "welcome",
            "you": cid,
            "module": table(self.m, self.state),
            "art": self.m.art,
            "colors": self.m.art_colors,
            "pictures": self.m.pictures,  # paths on the host: only screens running there can open them
            "state": self._view(),
            "log": [e.model_dump(mode="json") for e in self.state.log[-40:]],
            "seats": self._seats(),
            "started": self.started.is_set(),
        }

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
        if ev.get("type") == "improv":  # new places and people first, so later events can name them
            await self._broadcast({"t": "module", "module": table(self.m, self.state)})
            ev = {k: v for k, v in ev.items() if k != "reason"}  # the director's reasoning is for the keeper
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
            acted = False
            for idx, inv in enumerate(st.investigators):
                if st.ending:
                    break
                if inv.can_act:
                    st.active = idx
                    await self._turn(inv)
                    acted = True
            if not acted:  # nobody left standing
                break
        await self._broadcast({"t": "over", "ending": st.ending, "state": self._view()})

    async def _turn(self, inv: Any) -> None:
        who = self.sits.get(inv.id)
        await self._broadcast({
            "t": "turn", "investigator": inv.id, "name": inv.name,
            "player": self.names.get(who or "", ""), "ai": who is None, "state": self._view(),
        })  # fmt: skip
        if who is None:
            await self._ai_turn(inv)
            return
        queue = self.inbox[inv.id]
        while not queue.empty():  # nothing said before the turn counts
            queue.get_nowait()
        self.waiting = inv.id
        try:
            while True:
                text = await queue.get()
                if text is None:  # they left: the AI plays this turn for them
                    await self._ai_turn(inv)
                    return
                if not text.strip():
                    continue
                await self._event({"type": "said", "who": inv.name, "player": self.names.get(self.sits.get(inv.id) or "", ""), "text": text})
                await self._run(text, inv.id, ai=False)
                if not text.startswith("."):  # a dice command does not use up the turn
                    return
        finally:
            self.waiting = None

    async def _ai_turn(self, inv: Any) -> None:
        await self._event({"type": "status", "text": f"{inv.name}在想下一步…"})
        text = await self.cast.investigator(self.state, inv.id, situation(self.m, self.state))
        await self._event({"type": "said", "who": inv.name, "player": "", "text": text, "ai": True})
        await self._run(text, inv.id, ai=True)

    async def _run(self, text: str, inv_id: str, *, ai: bool) -> None:
        inp: Any = {"input": text}
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
            if choice not in ("push", "luck", "drop"):
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
        try:
            await self.play()
            if linger:
                await asyncio.Event().wait()
        finally:
            await self.cast.close()
            await runner.cleanup()

    async def _status(self, request: web.Request) -> web.Response:
        body = {"module": self.m.title, "seats": self._seats(), "started": self.started.is_set(), "clock": self.state.clock}
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))
