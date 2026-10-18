"""LAN play: one game, several players.

The host runs a Hub. It owns the only copy of the game (module, state, agents) and plays
the turns in order, one seat at a time. Every player's screen connects over a websocket,
receives the engine's events as they happen, and sends its own action when its turn
comes. Seats are humans or AI; a human who drops out is played by the AI until they
sit down again. Lines in full-width brackets are table talk and reach everyone at once.
A check asked for with .ra is that turn's action; the instant dice commands (.r, .st)
leave the turn open, and once the game is over they still work, growth rolls (.en) too.
A seated player throws their own dice: the roll prompt reaches them as an offer of kind
"roll", like a push offer, and the turn goes on when they answer (or leave). While the
table waits for a seated player, any seated player may ask the keeper to tell the latest
narration again (the turn loop does it, between actions); after the end, too. A seated
player may also ask for ideas (F8) at any time: the host's investigator agent thinks for
their seat from what the players have seen, and only they get the answer.

Messages are JSON objects with a "t" field:

    client -> hub   hello {name} · sit {investigator} · start · act {text} · choice {mode} · say {text}
                    · retell {nudge} · epilogue · suggest
    hub -> client   welcome · seats · turn · event {ev} · module · sync {state} · offer · say · ideas {ideas}
                    · over · error

Players' screens never hold the keeper's book while the game is on: they get the module
without its truth, keeper notes, NPC minds and anything not yet found, and a state without
agent memory or the director's plans. Once it has ended the whole module goes out, so every
screen can open the debrief (复盘); the epilogue is told on the host and reaches everyone.
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

from .. import config, roster
from ..engine import debrief, fog
from ..engine import graph as turn_graph
from ..engine.autoplay import situation
from ..engine.game import PLAY_WAIT, RETELL, Game, Told
from ..engine.state import GameState
from ..module.schema import Module
from ..rules import commands
from . import media

SYNC_AFTER = {"clue", "move", "time", "sanity", "damage", "flag", "sheet", "luck", "handout", "ending"}
SEATS = ("玩家", "AI", "不上场")
PRIVATE = {"transcripts", "notes", "usage", "turn_usage", "tally_at", "log", "memory", "improv", "missed", "visuals", "pending_turn"}
PRIVATE |= {"plugin_data", "plugin_versions", "plugin_sources", "plugin_receipts", "plugin_errors"}


def table(m: Module, st: GameState) -> dict[str, Any]:
    """The module as a player's screen may hold it: places, names, faces, pictures, and the
    clues and handouts already found (the rest arrive with the events that reveal them)."""
    d = m.model_dump(mode="json")
    d["plugins"] = []
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
        self.waiting: str | None = None  # the seat whose action the game waits for
        self.over = False  # the game has ended and its last turn is told
        self.desk = asyncio.Lock()  # after the end, one player's command at a time
        self.asks: asyncio.Queue = asyncio.Queue()  # retellings asked for mid-game: (client, nudge, narration), done between actions
        self.thinking: dict[str, asyncio.Task] = {}  # client -> the ideas being thought of for them (F8)
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
            text = str(msg.get("text", ""))[:500]
            if seat is None:
                await self._send(cid, {"t": "error", "text": "你在观战，没有自己的调查员"})
            elif self.over and commands.instant(text):  # growth rolls (.en) and the sheet still work
                await self._afterwards(seat, text)
            elif self.waiting != seat:
                await self._send(cid, {"t": "error", "text": "还没轮到你"})
            else:
                self.inbox[seat].put_nowait(text)
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
        elif t == "suggest":
            seat = self._seat_of(cid)
            if seat is None or self.state.ending:
                await self._send(cid, {"t": "ideas", "ideas": []})
            elif cid not in self.thinking:  # one at a time: each costs the host a call
                self.thinking[cid] = asyncio.create_task(self._ideas(cid, seat))

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
        self.over = True
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
                await self._asks()  # what the table asked for meanwhile: the last narration told again
                try:
                    text = await asyncio.wait_for(queue.get(), 1.0)
                except TimeoutError:  # while they think, a finished plan of the director's plays out
                    await self._interlude()
                    continue
                if text is None:  # they left: the AI plays this turn for them
                    await self._ai_turn(inv)
                    return
                if not text.strip():  # nothing said (a request waking the loop)
                    continue
                await self._event({"type": "said", "who": inv.name, "player": self.names.get(self.sits.get(inv.id) or "", ""), "text": text})
                try:
                    await self._run(text, inv.id, ai=False)
                except Exception as exc:
                    self.game.visuals.resume()
                    await self._send(who, {"t": "error", "retry": True, "text": f"这一回合暂未完成（{type(exc).__name__}），输入 /retry 接着讲，已结算的结果会保留"})
                    continue
                if not commands.instant(text):  # a check (.ra) is an action; .r, .st and the like leave the turn open
                    return
        finally:
            self.waiting = None

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
        elif self.waiting is None:
            await self._send(cid, {"t": "error", "text": "等这一回合结束再请守秘人重讲"})
        else:
            self.asks.put_nowait((cid, nudge, self.game.told))
            self.inbox[self.waiting].put_nowait("")  # wake the turn loop

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
        try:
            await self.play()
            if linger:
                await asyncio.Event().wait()
        finally:
            await self.game.close()
            await self.cast.close()
            await runner.cleanup()

    async def _status(self, request: web.Request) -> web.Response:
        body = {"module": self.m.title, "seats": self._seats(), "started": self.started.is_set(), "clock": self.state.clock}
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))
