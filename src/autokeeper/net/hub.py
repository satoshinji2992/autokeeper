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
their seat from what the players have seen, and only they get the answer. The story so far
(the 进展 page) is written here when the story has moved (engine/progress.py) and reaches every
screen with the state; a seated player may ask for it between rounds, once a round.

Messages are JSON objects with a "t" field:

    client -> hub   hello {name} · sit {investigator} · start · act {text} · choice {mode} · say {text}
                    · retell {nudge} · epilogue · suggest · recount
    client -> hub   (before the start) bring {investigator, portrait} or {pregen} · ready {on} · card {op ...}
    client -> hub   (in a round) done · undo
    client -> hub   answer {prompt, value, skip} · command {name, text}   (a rule plugin's question, a /command)
    hub -> client   welcome · seats · seated {investigator} · face {key, picture_data} · card · turn (the action being settled)
                    · pool {open, capacity, count, by, done, waiting, open_to, extra, mine {now, next}}
                    · event {ev} · module · sync {state} · offer · say · ideas {ideas}
                    · prelude {frames, art, colors, picture_data} · prelude_ready (the opening performance)
                    · over · error

Seats come from the host's plan: each of the module's pregens is a player's, an AI's or left out,
and there may be empty player seats besides. Before a new game starts, whoever sits in a player's
seat may put their own investigator there instead (bring: one of their cards, checked by
net/cards.py vet, with its portrait), another free pregen, or one made at the table on the host's
models (card; net/cards.py). The game starts when every player seat is taken and everyone says
they are ready, or when someone starts it now: an empty seat then gets a free pregen, or goes.
A saved game goes on with the investigators it has.

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

from aiohttp import WSCloseCode, WSMsgType, web
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import __version__, artkit, config, paths, roster
from ..agents.card import CardError
from ..engine import debrief, fog
from ..engine import graph as turn_graph
from ..engine.autoplay import situation
from ..engine.game import PLAY_WAIT, RETELL, Game, Told
from ..engine.state import GameState
from ..module.compiler import ensure_compiled
from ..module.schema import Module
from ..plugins.schema import does
from ..rules import commands
from ..rules.coc import Investigator
from . import cards, media
from .room import BLANKS

SYNC_AFTER = {"clue", "move", "time", "sanity", "damage", "flag", "sheet", "luck", "handout", "ending", "plugin_effect", "progress", "plugin_panel"}
SEATS = ("玩家", "AI", "不上场")
PER_PLAYER = 2  # actions one player may put in a round
# A round is a moment in real time (the user, 2026-10-21: 「时间同一轮应该是个现实 7s 的窗口」): from the
# first action put in, the others have this many seconds to act at the same time; then it is settled,
# and what comes later is the next round's. Nobody waits on a player who does not act.
WINDOW = 7.0
CARD_STEPS = 12  # 车卡 steps that call a model or the web, per player per minute
COSTLY = {"look_up", "find", "read", "propose", "build"}
PRIVATE = {"transcripts", "notes", "usage", "turn_usage", "tally_at", "log", "memory", "improv", "missed", "visuals", "pending_turn"}
PRIVATE |= {"pending_resolution", "pending_mcp"}
PRIVATE |= {"looks", "shown"}  # what the story changed and which big scenes came: pictures reach the screens, not notes
PRIVATE |= {"plugin_data", "plugin_versions", "plugin_sources", "plugin_receipts", "plugin_errors",
            "plugin_prompts", "plugin_timers", "plugin_marks", "plugin_serial"}
PRIVATE |= {"record"}  # the world record holds the backstage: secrets, plans, who knows what (world/)


def table(m: Module, st: GameState) -> dict[str, Any]:
    """The module as a player's screen may hold it: places, names, faces, pictures, and the
    clues and handouts already found (the rest arrive with the events that reveal them)."""
    d = m.model_dump(mode="json")
    d["plugins"] = []
    d["showcases"] = []  # big scenes (a final boss, the endings) spoil: each travels only when it comes (Hub._event)
    d["plugin_info"] = public_plugins(m, st)
    known = fog.bearings(m, st) | {st.location: m.location(st.location).name}
    visited = set(st.visited) | {st.location}
    d.update(truth="", keeper_notes="", flags={}, creatures=[], conclusions=[], events=[], endings=[])
    d["opening"] = ""  # material not yet spoken is not part of a player's knowledge
    d["locations"] = [loc for loc in d["locations"] if loc["id"] in known]
    # The screens draw their own map from this copy (tui/modals/mapview.py): the ways on it, what lies
    # in what on it, the minutes of the roads walked, and no other way out of anywhere.
    c = fog.chart(m, st)
    ways, roads = {frozenset(w) for w in c.roads + c.trails}, {frozenset(w) for w in c.roads}
    read, names = [fog.plain(text) for text in fog.kept(m, st)], fog.aliases(m)
    for loc in d["locations"]:
        here = loc["id"]
        loc["keeper"] = ""
        loc["looks"] = []  # what will become of a place is the keeper's to know
        loc["chase"] = []  # and where a chase from it would run: the way comes with the chase (chase_view)
        loc["exits"] = [pid for pid in loc["exits"] if frozenset((here, pid)) in ways]
        loc["travel_minutes"] = {pid: value for pid, value in loc["travel_minutes"].items() if frozenset((here, pid)) in roads}
        loc["within"] = c.within.get(here)
        if here not in visited:  # of its other names, those the players have read (and the map goes by)
            spelled = {fog.plain(a): a for a in [loc["name"], *loc["aliases"]]}
            loc["aliases"] = [spelled.get(a, a) for a in names.get(here, ()) if any(a in text for text in read)]
            loc.update(name=known[here], read_aloud="", features=[], art=None, weather=[], views=[])
        else:
            loc["features"] = []  # a keeper's searchable inventory is not a player's observation
            loc["views"] = [{**v, "features": [], "prompt": ""} for v in loc["views"]]  # nor what each picture is for
    d["npcs"] = [n for n in d["npcs"] if n["location"] == st.location or (st.npcs.get(n["id"]) and st.npcs[n["id"]].met)]
    for n in d["npcs"]:
        n.update(role="", personality="", voice="", goal="", knows=[], secrets=[], lies=[], clues=[], gates={}, stats={}, arc="", looks=[])
        n["status"] = m.npc(n["id"]).status  # the dead and the missing are not shown standing in a scene
        if n["location"] not in visited:
            n["location"] = None
    for c in d["clues"]:
        if c["id"] not in st.clues:
            c.update(title="", text="", feature="", check=None, npc=None, core=False, handout=None, points_to=[], sanity=None)
            c["where"] = ""
    for h in d["handouts"]:
        if h["id"] not in st.handouts:
            h.update(title="", text="")
    from ..world import knowledge
    from ..world.record import PLAYERS
    from ..world.store import World

    seen = fog.things(m, st)
    view = World(m, st, initialize=False)
    party = {inv.id for inv in st.investigators}
    def visible(item: dict) -> bool:
        if item["id"] in st.record.entities:
            return knowledge.item_known(view, item["id"], PLAYERS)
        return (item["owner"] in party or (not item["owner"] and not item["hidden"] and item["equipment"] != "concealed"
                and (item["where"] in visited or item["id"] in seen)))

    d["items"] = [item for item in d["items"] if visible(item)]
    for item in d["items"]:
        ent = st.record.entities.get(item["id"])
        if ent is not None and ent.at in party:
            item.update(owner=ent.at, equipment=ent.equipment, quantity=ent.quantity, status=ent.status, hidden=ent.hidden)
        else:
            item.update(owner="", equipment="carried", quantity=1, status="", hidden=False)
    for e in d["events"]:
        e.update(title="", text="", read_aloud="", performance="", art="")  # an event's picture comes with its moment
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
            "commands": [[c.name, c.description] for c in plugin.commands], "does": does(plugin.capabilities),
            "paused": identity in st.plugin_errors,
        })
    return out


def _builder(cast: Any, m: Module) -> Any:
    """A Builder on the host's models, for one player's 车卡."""
    from ..agents.card import Builder

    return Builder(getattr(cast, "cfg", None) or config.load(), m)


def room_key_file(port: int):
    """Where a running room keeps its host key: only someone on this machine can read it."""
    return paths.room_key(port)


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



def _handout_keys(h: dict) -> list[str]:
    """A handout's picture keys in a module dump: its art and the book's pages (Handout.original_keys)."""
    return [*h.get("images", []), *(f"{h['id']}~original{i + 1}" for i in range(len(h.get("originals", []))))]

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
        self.window = WINDOW  # seconds a round stays open once someone acted (a test shortens it)
        self.m = Module.load(self.dir)
        ensure_compiled(self.m, self.dir)  # the keeper reads the module's skill package
        self.fresh = state is None
        extra: list[str] = []
        if state is None:
            plan = [("AI" if s.lower() == "ai" else s) for s in seats] if seats else ["玩家"] + ["AI"] * (len(self.m.pregens) - 1)
            if odd := [s for s in plan if s not in SEATS]:
                raise ValueError(f"座位类型仅支持 {' / '.join(SEATS)}，无效值：{'、'.join(odd)}")
            extra = plan[len(self.m.pregens):]  # beyond the pregens: empty seats for players' own cards
            if any(s != "玩家" for s in extra):
                raise ValueError(f"此模组提供 {len(self.m.pregens)} 名预设调查员，额外座位仅支持玩家控制。")
            if len(extra) > BLANKS:
                raise ValueError(f"空座位最多 {BLANKS} 个")
            chosen = [(p, s) for p, s in zip(self.m.pregens, plan, strict=False) if s != "不上场"]
            if not chosen and not extra:
                raise ValueError("至少需启用一名调查员。")
            party = [p.model_copy(deep=True) for p, _ in chosen]
            human = [p.id for p, s in chosen if s == "玩家"]
            state = Game.new(self.m, party, slot=slot or f"lan-{self.m.id}-{time.strftime('%m%d-%H%M%S')}", human=human)
        self.state = state
        self.blank: list[str] = [f"seat-{i + 1}" for i in range(len(extra))] if self.fresh else []  # player seats with nobody's card yet
        roster.attach(self.m, state.investigators)
        for inv in state.investigators:  # brought to an earlier session of this game (bring)
            face = paths.table_art() / f"{inv.portrait}.jpg"
            if inv.portrait and inv.portrait not in self.m.pictures and face.is_file():
                self.m.pictures[inv.portrait] = str(face)
        self.cast = cast or Cast(config.load(), self.m)
        self.game = Game(self.m, state, self.cast, rules or Rules(), emit=self._event)
        self.game.director_wait = PLAY_WAIT
        self.game.write_progress = True  # the 进展 page on every screen, through the sync
        self.game.world_later = True  # NPCs decide between rounds, not while the table waits
        self.game.world.reviewing = True  # the director looks backstage after a night or a journey (Game._look)
        self.graph = turn_graph.build(self.game, InMemorySaver())
        self.game.checkpoint = self._save
        self.thread = {"configurable": {"thread_id": state.slot}}
        self.clients: dict[str, web.WebSocketResponse] = {}
        self.names: dict[str, str] = {}
        self.sits: dict[str, str | None] = {inv_id: None for inv_id in [*state.human, *self.blank]}  # human seat -> client
        self.inbox: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in self.sits}
        self.choices: dict[str, asyncio.Queue] = {inv_id: asyncio.Queue() for inv_id in self.sits}
        self.ready: set[str] = set()  # player seats whose player is happy with their card
        self.origin: dict[str, str] = {}  # seat -> "own" (a card of the player's) or "new" (made at the table)
        self.notes: dict[str, str] = {}  # seat -> a word for the host about the card (era_note)
        self.builders: dict[str, Any] = {}  # client -> the card being made for them (net/cards.py)
        self.carding: dict[str, asyncio.Task] = {}  # client -> the 车卡 step under way
        self.card_steps: dict[str, list[float]] = {}  # client -> when their costly 车卡 steps began
        self.card_builder = lambda: _builder(self.cast, self.m)
        self.started = asyncio.Event()
        self.open = False  # the round's action pool takes actions
        self.pool: list[tuple[str, str]] = []  # (seat, action) in the order they came in
        self.done: set[str] = set()  # seats that said they have nothing more this round
        self.early: list[tuple[str, str]] = []  # (seat, action) for the next round: sent while the keeper told, or with no room
        self.instant: asyncio.Queue = asyncio.Queue()  # (seat, .r / .st ...) to run while the pool is open
        self.wake = asyncio.Event()  # something for the round loop: an action, a request, someone left
        self.closes_at: float | None = None  # when the open round's window ends (loop time), once someone acted
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
        self.plays: asyncio.Queue = asyncio.Queue()  # answers to plugins' questions and /commands, done between actions
        self.game.plugins.away = lambda seat: self.sits.get(seat) is None  # an empty seat's questions take the default
        self.sent: dict[str, str] = {}  # art key -> the picture the screens were sent for it
        self.room: dict[str, Any] = {}  # how to reach this room, for every lobby: addresses, ports (net/room.py)

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
                        await self._send(cid, {"t": "error", "text": f"无效消息：{exc}"})
        finally:
            self.clients.pop(cid, None)
            self._drop_card(cid)
            for inv_id, who in self.sits.items():
                if who == cid:  # whoever waits for them gets an empty answer: the AI plays on
                    self.sits[inv_id] = None
                    self.ready.discard(inv_id)
                    self.early = [(seat, text) for seat, text in self.early if seat != inv_id]
                    self.inbox[inv_id].put_nowait(None)
                    self.choices[inv_id].put_nowait(None)
                    self.wake.set()
            await self._share_seats()
        return ws

    async def _on(self, cid: str, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "hello":
            self.names[cid] = (msg.get("name") or "").strip()[:16] or f"玩家{len(self.names) + 1}"
            if msg.get("key") and secrets.compare_digest(str(msg["key"]), self.key):
                self.hosts.add(cid)
                self.names[cid] += "·房主"
            await self._send(cid, self._welcome(cid))
            await self._share_seats()
            if (theirs := msg.get("version")) and theirs != __version__:  # the host hears it; the friend's own lobby says so too
                for host in self.hosts - {cid}:
                    await self._send(host, {"t": "error", "text": f"{self.names[cid]} 的版本和你不一样，可能会出错"})
        elif t == "sit":
            await self._sit(cid, msg.get("investigator", ""))
        elif t == "start":
            await self._begin(cid)
        elif t == "bring":
            await self._bring(cid, msg)
        elif t == "ready":
            await self._ready(cid, bool(msg.get("on", True)))
        elif t == "card":
            await self._card(cid, msg)
        elif t == "act":
            seat = self._seat_of(cid)
            text = str(msg.get("text", ""))[:500]
            if seat is None:
                await self._send(cid, {"t": "error", "text": "观战模式下无可操作的调查员。"})
            elif self.over and commands.instant(text):  # growth rolls (.en) and the sheet still work
                await self._afterwards(seat, text)
            elif self.over:
                await self._send(cid, {"t": "error", "text": "本局已结束。"})
            elif self.retrying == seat and text.strip().startswith("/retry"):
                self.inbox[seat].put_nowait(text)
            elif text.strip().startswith("/") and not text.strip().startswith("/kp "):
                await self._send(cid, {"t": "error", "text": "当前无中断回合。"})
            elif text.strip().startswith("/kp ") or commands.instant(text):  # questions do not occupy an action slot
                self.instant.put_nowait((seat, text))
                self.wake.set()
            elif not text.strip():
                return
            elif self.open and self._may_add(seat):
                self.pool.append((seat, text))
                self.done.discard(seat)
                self._start_window()
                self.wake.set()
                await self._pool()
            elif sum(1 for s, _ in self.early if s == seat) >= PER_PLAYER:
                await self._send(cid, {"t": "error", "text": f"下一轮已有 {PER_PLAYER} 条行动，可用 /undo 撤回上一条。"})
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
                await self._send(cid, {"t": "error", "text": "本局结束后可生成后日谈。"})
            else:
                async with self.desk:
                    await self._afterword()
        elif t == "engine":
            if cid not in self.hosts:
                await self._send(cid, {"t": "error", "text": "模型仅限房主切换。"})
            elif isinstance(msg.get("choice"), dict):
                self.swaps.put_nowait((cid, msg["choice"]))
                await self._send(cid, {"t": "error", "text": "模型切换已排队，将于当前叙述结束后生效。"})
        elif t == "recount":  # the story so far, now (Ctrl+Y): between rounds, once a round
            why = ("观战模式下无法执行此操作。" if self._seat_of(cid) is None else "本轮正在结算，结束后可整理进展。"
                   if not (self.open or self.over) else self.game.recount())
            if why:
                await self._send(cid, {"t": "error", "text": why})
        elif t == "suggest":
            seat = self._seat_of(cid)
            if seat is None or self.state.ending:
                await self._send(cid, {"t": "ideas", "ideas": []})
            elif cid not in self.thinking:  # one at a time: each costs the host a call
                self.thinking[cid] = asyncio.create_task(self._ideas(cid, seat))
        elif t in ("answer", "command"):  # a rule plugin's question, or its /command: for your own seat only
            seat = self._seat_of(cid)
            if seat is None:
                await self._send(cid, {"t": "error", "text": "观战模式下无可操作的调查员。"})
            else:
                self.plays.put_nowait((cid, seat, msg))
                self.wake.set()
        elif t == "house_rule":  # the rule's code runs on the host's machine: only the host asks for one
            text = str(msg.get("text", "")).strip()[:500]
            if cid not in self.hosts:
                await self._send(cid, {"t": "error", "text": "自定义规则仅限房主添加。"})
            elif text:
                self.rules.put_nowait((cid, text))
                self.wake.set()
                await self._send(cid, {"t": "error", "text": "规则请求已排队，将于当前叙述结束后处理。"})

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
            await self._send(cid, {"t": "error", "text": "此座位由 AI 控制，无法入座。"})
            return
        if self.sits[inv_id] not in (None, cid):
            await self._send(cid, {"t": "error", "text": "此座位已被占用。"})
            return
        for seat, who in self.sits.items():
            if who == cid and seat != inv_id:
                self.sits[seat] = None
                self.ready.discard(seat)
        self.sits[inv_id] = cid
        await self._share_seats()
        if not self.bringing and all(self.sits.values()):  # a saved game: every human seat is taken, it goes on
            self.started.set()
        await self._maybe_begin()

    def _seat_of(self, cid: str) -> str | None:
        return next((seat for seat, who in self.sits.items() if who == cid), None)

    # -- choosing investigators, before the start -----------------------------------------------
    @property
    def bringing(self) -> bool:
        """Seats may still change their investigator: a new game that has not begun."""
        return self.fresh and not self.started.is_set() and not self.state.log

    async def _no(self, cid: str, text: str) -> None:
        await self._send(cid, {"t": "error", "text": text})

    async def _ready(self, cid: str, on: bool) -> None:
        seat = self._seat_of(cid)
        if seat is None or not self.bringing:
            return
        if on and seat in self.blank:
            await self._no(cid, "请先选择角色。")
            return
        (self.ready.add if on else self.ready.discard)(seat)
        await self._share_seats()
        await self._maybe_begin()

    async def _maybe_begin(self) -> None:
        """Every player seat taken, with a card, and everyone ready: the game begins."""
        if self.bringing and all(self.sits.values()) and not self.blank and set(self.sits) <= self.ready:
            await self._begin(None)

    async def _begin(self, cid: str | None) -> None:
        """Start now. An empty seat someone sits in gets a pregen nobody plays; the others go."""
        if self.started.is_set():
            return
        if self.bringing:
            spares = [p for p in self.m.pregens if p.id not in {i.id for i in self.state.investigators}]
            if not self.state.investigators and not (spares and any(self.sits.get(seat) for seat in self.blank)):
                if cid is not None:
                    await self._no(cid, "尚无已选角色，无法开始游戏。")
                return
            changed = False
            for seat in list(self.blank):
                who = self.sits.get(seat)
                spare = next((p for p in self.m.pregens if p.id not in {i.id for i in self.state.investigators}), None)
                if who is not None and spare is not None:
                    await self._send(who, {"t": "seated", "investigator": self._install(seat, spare.model_copy(deep=True), "")})
                else:
                    self.blank.remove(seat)
                    for table in (self.sits, self.inbox, self.choices):
                        table.pop(seat, None)
                    if who is not None:
                        await self._no(who, "预设角色均已分配，当前仅可观战。")
                changed = True
            for client in list(self.builders):
                self._drop_card(client)
            if changed:
                await self._broadcast({"t": "sync", "state": self._view()})
                await self._share_seats()
        self.started.set()

    def _install(self, seat: str, inv: Investigator, origin: str) -> str:
        """`inv` into a player's seat in place of whoever was there: the party, the human seats
        and the seat's queues under its new id. Only before the start, so nothing waits on them."""
        st = self.state
        taken = {i.id for i in st.investigators} - {seat}
        if origin or inv.id in taken:  # a brought card gets an id of its own
            inv.id = f"pc-{uuid.uuid4().hex[:6]}"
        if seat in self.blank:
            self.blank.remove(seat)
            st.investigators.append(inv)
            st.human.append(inv.id)
        else:
            st.investigators[next(i for i, x in enumerate(st.investigators) if x.id == seat)] = inv
            st.human = [inv.id if h == seat else h for h in st.human]
        for table in (self.sits, self.inbox, self.choices):
            table[inv.id] = table.pop(seat)
        self.ready.discard(seat)
        self.notes.pop(seat, None)
        self.origin.pop(seat, None)
        if origin:
            self.origin[inv.id] = origin
        return inv.id

    async def _bring(self, cid: str, msg: dict[str, Any]) -> None:
        """A player puts an investigator in their seat: their own card, or a free pregen."""
        seat = self._seat_of(cid)
        if not self.bringing:
            await self._no(cid, "继续存档时不可更换角色。" if not self.fresh else "游戏已开始，不可更换角色。")
            return
        if seat is None:
            await self._no(cid, "入座后可选择角色。")
            return
        face = ""
        if pregen := msg.get("pregen"):
            found = next((p for p in self.m.pregens if p.id == pregen), None)
            if found is None:
                await self._no(cid, "未找到此角色。")
                return
            if found.id == seat:
                return
            if found.id in {i.id for i in self.state.investigators}:
                await self._no(cid, "此角色已分配至其他座位。")
                return
            inv, origin = found.model_copy(deep=True), ""
        else:
            try:
                inv = cards.vet(msg.get("investigator"), "")
            except cards.Refused as exc:
                await self._no(cid, str(exc))
                return
            origin = "new" if msg.get("made") else "own"
            picture = media.unpack(msg.get("portrait") or "")
            inv.portrait = Path(picture).stem if picture else ""
            if picture:
                self.m.pictures[inv.portrait] = self.sent[inv.portrait] = picture
                face = str(msg["portrait"])
        note = "" if origin == "" else cards.era_note(inv, self.m)
        new = self._install(seat, inv, origin)
        if note:
            self.notes[new] = note
        if face:
            await self._broadcast({"t": "face", "key": inv.portrait, "picture_data": face})
        await self._broadcast({"t": "sync", "state": self._view()})
        await self._send(cid, {"t": "seated", "investigator": new})
        await self._share_seats()

    # -- 车卡 for a seated player, on the host's models --------------------------------------------
    async def _card(self, cid: str, msg: dict[str, Any]) -> None:
        op, n = str(msg.get("op", "")), msg.get("n")
        if op in ("cancel", "end"):
            if task := self.carding.pop(cid, None):
                task.cancel()
            if op == "end":
                self._drop_card(cid)
            return
        why = ""
        if not self.bringing or self._seat_of(cid) is None:
            why = "仅入座玩家可在开局前创建调查员。"
        elif op == "begin":
            self._drop_card(cid)  # one card at a time for each player
            self.builders[cid] = self.card_builder()
            return
        elif cid not in self.builders:
            why = "请先开始创建调查员。"
        elif cid in self.carding:
            why = "上一步尚未完成，请稍后重试。"
        elif op in COSTLY:
            now = time.monotonic()
            recent = [at for at in self.card_steps.get(cid, []) if now - at < 60]
            if len(recent) >= CARD_STEPS:
                why = "已达到请求频率限制，请稍后重试。"
            self.card_steps[cid] = recent + ([] if why else [now])
        if why:
            if n is not None:
                await self._send(cid, {"t": "card", "n": n, "error": why})
            return
        self.carding[cid] = asyncio.create_task(self._card_step(cid, self.builders[cid], op, msg, n))

    async def _card_step(self, cid: str, builder: Any, op: str, msg: dict[str, Any], n: Any) -> None:
        from ..harness import BudgetExceeded

        try:
            reply = await cards.step(builder, op, msg, self._card_dir(builder))
        except asyncio.CancelledError:
            return
        except CardError as exc:
            reply = {"error": str(exc)}
        except BudgetExceeded:
            reply = {"error": "房主设的花费上限到了，车不了卡"}
        except Exception:  # the model is down, or answered nonsense twice
            reply = {"error": "无法读取人物卡，请检查文件内容或格式。" if op == "read" else "房主设备处理失败，可稍后重试。"}
        finally:
            if self.carding.get(cid) is asyncio.current_task():
                self.carding.pop(cid, None)
        await self._send(cid, {"t": "card", "n": n, **reply})

    def _card_dir(self, builder: Any) -> Path:
        return paths.card_work(f"lan-{builder.id}")

    def _drop_card(self, cid: str) -> None:
        if task := self.carding.pop(cid, None):
            task.cancel()
        if (builder := self.builders.pop(cid, None)) is not None:
            import shutil

            shutil.rmtree(self._card_dir(builder), ignore_errors=True)

    # -- what clients see ------------------------------------------------------------------
    def _view(self) -> dict[str, Any]:
        view = self.state.model_dump(mode="json", exclude=PRIVATE)
        view["flags"], view["fired"] = [], []
        for key, people in (("fight", "fighters"), ("chase", "runners")):  # what the module says of a foe is the keeper's
            if view.get(key):
                for f in view[key][people]:
                    f["notes"] = ""
                view[key]["orders"] = {}
        if view.get("pending_push"):
            view["pending_push"] = {k: v for k, v in view["pending_push"].items() if k not in ("clues", "flags")}
        return view

    def _seats(self, cid: str | None = None) -> list[dict[str, Any]]:
        """Who sits where, as everyone sees it; the host (cid) also gets a word on brought cards."""
        out = []
        for inv in self.state.investigators:
            who = self.sits.get(inv.id)
            out.append({
                "investigator": inv.id, "name": inv.name, "occupation": inv.occupation,
                "human": inv.id in self.sits, "player": self.names.get(who or "", ""), "online": who is not None,
                "origin": self.origin.get(inv.id, ""), "ready": inv.id in self.ready,
            })  # fmt: skip
        for seat in self.blank:
            who = self.sits.get(seat)
            out.append({"investigator": seat, "name": "", "occupation": "", "human": True, "blank": True,
                        "player": self.names.get(who or "", ""), "online": who is not None, "origin": "", "ready": False})
        if cid in self.hosts:
            for row in out:
                row["note"] = self.notes.get(row["investigator"], "")
        return out

    async def _share_seats(self) -> None:
        for cid in list(self.clients):
            await self._send(cid, {"t": "seats", "seats": self._seats(cid), "bring": self.bringing})

    def _welcome(self, cid: str) -> dict[str, Any]:
        book = self._book()
        keys = self._art_keys(book)
        return {
            "t": "welcome",
            "you": cid,
            "host": cid in self.hosts,
            "version": __version__,  # a client on another version is told so (tui/screens/net.py)
            "module": book,
            "art": {k: v for k, v in self.m.art.items() if k in keys},
            "colors": {k: v for k, v in self.m.art_colors.items() if k in keys},
            "picture_data": self._shared(self._pictures(book, keys)),
            "state": self._view(),
            "log": [e.model_dump(mode="json") for e in self.state.log[-40:]],
            "seats": self._seats(cid),
            "bring": self.bringing,  # seats may still change their investigator
            "started": self.started.is_set(),
            "room": self.room,
        }

    def _art_keys(self, book: dict) -> set[str]:
        return ({x.get("art") for x in book["locations"] + book["items"]} | {p.portrait for p in self.m.pregens}
                | {v["key"] for x in book["locations"] for v in x.get("views", [])} | {c.get("art") for c in book["clues"]}
                | {x.get("portrait") for x in book["npcs"]} | {i.portrait for i in self.state.investigators}
                | {key for h in book["handouts"] for key in _handout_keys(h)})

    def _pictures(self, book: dict, keys: set[str]) -> dict[str, str]:
        handouts = {key for h in book["handouts"] for key in _handout_keys(h)}
        return {k: media.pack(v, max_size=1600 if k in handouts else 512)
                for k, v in self.m.pictures.items() if k in keys}

    def _shared(self, pictures: dict[str, str]) -> dict[str, str]:
        self.sent.update({k: self.m.pictures[k] for k, data in pictures.items() if data})
        return pictures

    async def _share_book(self) -> None:
        book = self._book()
        keys = self._art_keys(book)
        await self._broadcast({"t": "module", "module": book,
                               "art": {k: v for k, v in self.m.art.items() if k in keys},
                               "colors": {k: v for k, v in self.m.art_colors.items() if k in keys}})
        # Pictures that came into view (a place first visited, a face first met, one the story
        # redrew while nobody could see it) arrive the way new pictures do: as an art event.
        handouts = {key for h in book["handouts"] for key in _handout_keys(h)}  # these travel with their handout
        for key in sorted(k for k in keys - handouts if k):  # places with no art key give None
            path = self.m.pictures.get(key)
            if path and self.sent.get(key) != path and (data := media.pack(path)):
                self.sent[key] = path
                art = self.m.art.get(key, "")
                await self._broadcast({"t": "event", "ev": {"type": "art", "key": key, "art": art, "status": "ready",
                                                            "mask": self.m.art_colors.get(key) or artkit.auto_mask(art),
                                                            "picture_data": data}})

    def _book(self) -> dict[str, Any]:
        """The module for the players' screens: the table's copy while the game is on, the
        whole book once it has ended (the debrief lays it open anyway), but for big scenes
        that never came."""
        return self.m.model_dump(mode="json", exclude={"showcases"}) | {"showcases": []} if self.state.ending else table(self.m, self.state)

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
        if ev.get("type") in ("plugin_prompt", "plugin_closed"):  # to the player asked, and nobody else
            who = self.sits.get(ev.get("who", ""))
            if who is not None:
                await self._send(who, {"t": "event", "ev": ev})
            return
        if ev.get("type") == "flag":
            await self._broadcast({"t": "sync", "state": self._view()})
            return
        if ev.get("type") in ("improv", "move", "npc", "performance", "clue", "handout", "people"):
            await self._share_book()
            ev = {k: v for k, v in ev.items() if k != "reason"}  # the director's reasoning is for the keeper
            if ev.get("type") == "handout":
                book = self._book()
                handout = next((h for h in book["handouts"] if h["id"] == ev.get("id")), None)
                if handout:
                    ev["picture_data"] = self._pictures(book, set(_handout_keys(handout)))
        elif ev.get("type") == "art":
            if ev["key"] not in self._art_keys(self._book()):
                return
            ev = {k: v for k, v in ev.items() if k in ("type", "key", "art", "mask", "status")}
            path = self.m.pictures.get(ev["key"])
            if path:
                ev["picture_data"] = media.pack(path)
                self.sent[ev["key"]] = path
        elif ev.get("type") == "view":  # another picture of the place: the picture first, if the screens lack it
            key = ev.get("key", "")
            path = self.m.pictures.get(key)
            if path and self.sent.get(key) != path and (data := media.pack(path)):
                self.sent[key] = path
                art = self.m.art.get(key, "")
                await self._broadcast({"t": "event", "ev": {"type": "art", "key": key, "art": art, "status": "ready",
                                                            "mask": self.m.art_colors.get(key) or artkit.auto_mask(art),
                                                            "picture_data": data}})
        elif ev.get("type") == "ending":  # the book opens with the ending, before any screen knows it is over
            await self._broadcast({"t": "module", "module": self._book()})
        elif ev.get("type") in ("plugin_effect", "plugin_show", "plugin_panel", "plugin_answered"):  # public; the id stays on the host
            ev = {k: v for k, v in ev.items() if k != "plugin"}
        elif ev.get("type") == "showcase":  # its moment has come: the picture itself, never the host's path
            data = media.pack(ev.get("picture", ""))
            if not data:
                return
            ev = {"type": "showcase", "id": ev.get("id", ""), "kind": ev.get("kind", ""), "title": ev.get("title", ""), "picture_data": data}
        await self._broadcast({"t": "event", "ev": ev})
        if ev.get("type") in SYNC_AFTER:
            await self._broadcast({"t": "sync", "state": self._view()})

    # -- the game ---------------------------------------------------------------------------
    async def play(self) -> None:
        await self.started.wait()
        await self._broadcast({"t": "begin"})
        st = self.state
        if self.fresh and not st.log:
            try:
                await self._prelude()
                await self.game.start()
            except Exception as exc:  # no opening told: the table can still play, and is told why
                self.game.visuals.resume()
                await self._broadcast({"t": "say", "from": "系统", "text": f"开场没能讲完（{type(exc).__name__}），可以直接开始行动"})
            self._save()
            await self._broadcast({"t": "sync", "state": self._view()})
        while not st.ending:
            if not any(inv.can_act for inv in st.investigators):  # nobody left standing
                break
            while not self.clients:  # nobody at the table: the AI does not play on alone, spending
                await asyncio.sleep(1.0)
            await self._collect()
            await self._settle()
        self.over = True
        await self._broadcast({"t": "over", "ending": st.ending, "state": self._view()})

    async def _prelude(self) -> None:
        """Every screen plays the opening performance (module/prelude.py) while the director adapts
        the module to the party; the screens hear it is ready once that is done and the shots
        have had their time, so the table goes into the game together."""
        from ..module import prelude
        from ..tui.screens.prelude import duration

        shots = prelude.frames(self.m)
        keys = {shot.art for shot in shots if shot.art}
        begun = time.monotonic()
        await self._broadcast({"t": "prelude", "frames": [shot.model_dump(mode="json") for shot in shots],
                               "art": {k: v for k, v in self.m.art.items() if k in keys},
                               "colors": {k: v for k, v in self.m.art_colors.items() if k in keys},
                               "picture_data": self._shared({k: media.pack(v) for k, v in self.m.pictures.items() if k in keys})})
        try:
            await self.game.prepare_opening()
            await asyncio.sleep(max(0.0, duration(shots) - (time.monotonic() - begun)))
        finally:
            self.game.prelude_played = True
            await self._broadcast({"t": "prelude_ready"})

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

    def _start_window(self) -> None:
        """The first action of the round starts its window (WINDOW)."""
        if self.open and self.closes_at is None and self.pool:
            self.closes_at = asyncio.get_running_loop().time() + self.window

    def _left(self) -> float | None:
        """Seconds left in the round's window, while one runs."""
        if not self.open or self.closes_at is None:
            return None
        return max(0.0, self.closes_at - asyncio.get_running_loop().time())

    async def _pool(self) -> None:
        """The round as everyone sees it, and each player's own lines: in it, and waiting for the next."""
        names = {inv.id: inv.name for inv in self.state.investigators}
        waiting = self._waiting_for() if self.open else []
        public = {"t": "pool", "open": self.open, "capacity": self.capacity(), "count": len(self.pool),
                  "by": {seat: self._count(seat) for seat in self._players()}, "done": sorted(self.done),
                  "waiting": [names[i] for i in waiting], "open_to": waiting, "extra": names.get(self._extra() or "", ""),
                  "closes_in": self._left()}
        for cid in list(self.clients):
            seat = self._seat_of(cid)
            mine = {"now": [text for s, text in self.pool if s == seat], "next": [text for s, text in self.early if s == seat]}
            await self._send(cid, {**public, "mine": mine if seat else {}})

    async def _collect(self) -> None:
        """Open the pool and wait until it is full or everyone is done. What was said for this
        round while the keeper told the last goes in first. Meanwhile the table's requests
        (a retelling, the host's model switch, .r and .st) are done, and a finished plan of the
        director's plays out."""
        self.pool, self.done, self.open, self.closes_at = [], set(), True, None
        for queue in self.inbox.values():
            while not queue.empty():
                queue.get_nowait()
        early, self.early = self.early, []
        players = self._players()
        for seat, text in early:  # in the order it was said; what finds no room waits a round more
            if seat in players:
                (self.pool if self._may_add(seat) else self.early).append((seat, text))
        self._start_window()  # said while the keeper told: the moment starts as the round opens
        await self._event({"type": "status", "text": ""})
        await self._pool()
        try:
            while True:  # once at least: a .r sent while the keeper told runs even if the round is already full
                await self._swap()
                await self._house_rules()
                await self._plays()
                await self._asks()
                while not self.instant.empty():
                    seat, text = self.instant.get_nowait()
                    await self._safely(self._afterwards(seat, text), f"「{text[:20]}」", text)
                    await self._pool()
                left = self._left()
                if not self._waiting_for() or left == 0:  # everyone has acted, or the moment has passed
                    break
                try:
                    await asyncio.wait_for(self.wake.wait(), min(1.0, left) if left is not None else 1.0)
                except TimeoutError:  # while they think, a finished plan of the director's plays out
                    if self._left() is None:
                        await self._safely(self._interlude(), "幕间")
                self.wake.clear()
        finally:
            self.open, self.closes_at = False, None
        await self._pool()

    async def _settle(self) -> None:
        """The pooled actions in the order they came in, then one for each seat nobody is
        playing (the AI's, and a player's who left without putting anything in). They happen
        at once: each is ruled and rolled in that order, the clock moves by the longest of them,
        and the keeper tells the round in one go after the last (the user, 2026-10-21: 「可以行动
        不排着吗，因为可能是多人同时行动」)."""
        st = self.state
        acted = {seat for seat, _ in self.pool}
        order = [*self.pool, *[(inv.id, None) for inv in st.investigators
                               if inv.id not in acted and inv.id not in self.done and self.sits.get(inv.id) is None]]
        self.game.begin_round()  # the round's actions happen at once: one clock, one telling (Game.narrate)
        try:
            await self._settle_round(order)
        finally:
            self.game.end_round()
        if st.held and not st.ending:  # the round's last action was not told (it failed, or was let go): told now
            await self._safely(self._tell_held(), "这一轮的叙述")

    async def _tell_held(self) -> None:
        self.game.emit = self._event
        await self.game.tell_held()
        self._save()
        await self._broadcast({"t": "sync", "state": self._view()})

    def _acts(self, seat: str) -> bool:
        inv = next((i for i in self.state.investigators if i.id == seat), None)
        return inv is not None and inv.can_act

    async def _settle_round(self, order: list[tuple[str, str | None]]) -> None:
        st = self.state
        for n, (seat, text) in enumerate(order, 1):
            inv = next((i for i in st.investigators if i.id == seat), None)
            if st.ending:
                return
            if inv is None or not inv.can_act:
                continue
            self.game.holding = any(self._acts(later) for later, _ in order[n:])  # someone after: they tell it
            await self._plays()  # what was answered meanwhile goes in before the next action
            st.active = st.investigators.index(inv)
            who = self.sits.get(seat)
            await self._broadcast({
                "t": "turn", "investigator": seat, "name": inv.name, "player": self.names.get(who or "", ""),
                "ai": text is None, "index": n, "total": len(order), "state": self._view(),
            })  # fmt: skip
            if text is None:
                try:
                    await self._ai_turn(inv)
                except Exception as exc:
                    self.game.visuals.resume()
                    await self._event({"type": "status", "text": ""})
                    await self._unattended(inv, exc)
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
                    if who is None:  # they left meanwhile: the table finishes it without them
                        await self._unattended(inv, exc)
                        break
                    queue = self.inbox[seat]
                    while not queue.empty():  # a None left by an earlier disconnect is not an answer now
                        queue.get_nowait()
                    await self._broadcast({"t": "say", "from": "系统", "text": f"{inv.name}的行动中断了（{type(exc).__name__}），等{self.names.get(who, '他')}输入 /retry 接着讲"})
                    await self._send(who, {"t": "error", "retry": True, "text": f"这一回合暂未完成（{type(exc).__name__}），输入 /retry 接着讲，已结算的结果会保留"})
                    self.retrying = seat
                    try:
                        text = await queue.get()
                    finally:
                        self.retrying = None
                    if text is None:  # they left while asked
                        await self._unattended(inv, exc)
                        break

    async def _unattended(self, inv: Any, exc: BaseException) -> None:
        """A turn that failed with nobody to /retry it (an AI seat, a player who left): the table
        resumes it itself, twice, and then lets it go: what was settled stays, the journal is
        cleared, the round goes on. Before, an AI's failure closed the room, and a turn left
        unfinished refused every action after it (graph.route)."""
        for _ in range(2):
            if not self.state.pending_turn:
                break
            try:
                await self._run("/retry", inv.id, ai=True)
                return
            except Exception as again:
                self.game.visuals.resume()
                exc = again
        if self.state.pending_turn:
            self.state.pending_turn.clear()
            self._save()
            note = f"{inv.name}这一回合没能讲完（{type(exc).__name__}），已结算的结果保留，接着往下"
        else:
            note = f"{inv.name}这一轮没有行动（{type(exc).__name__}）"
        await self._broadcast({"t": "say", "from": "系统", "text": note})
        await self._broadcast({"t": "sync", "state": self._view()})

    async def _safely(self, work: Any, what: str, said: str = "") -> None:
        """Table business between actions (.r, /kp, an interlude): a failure is told, not the end
        of the room. A command `said` that failed half-way is not left as an unfinished turn that
        would refuse every action after it: nobody /retries a question to the keeper."""
        try:
            await work
        except Exception as exc:
            self.game.visuals.resume()
            await self._event({"type": "status", "text": ""})
            if said and self.state.pending_turn.get("input") == said:
                self.state.pending_turn.clear()
                self._save()
            await self._broadcast({"t": "say", "from": "系统", "text": f"{what}没能完成（{type(exc).__name__}）"})

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
            await self._send(cid, {"t": "error", "text": "观战模式不支持请求重述。"})
        elif why:
            await self._send(cid, {"t": "error", "text": why})
        elif self.over:
            async with self.desk:
                await self._tell_again(cid, nudge, self.game.told)
        elif not self.open:
            await self._send(cid, {"t": "error", "text": "本轮叙述结束后可请求重述。"})
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
                cfg = config.load(choice=choice)
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

    async def _plays(self) -> None:
        """Answers to rule plugins' questions and their /commands, from the players they were
        for; then any question nobody answered in time takes its default."""
        plugins = self.game.plugins
        while not self.plays.empty():
            cid, seat, msg = self.plays.get_nowait()
            self.game.emit = self._event
            if msg["t"] == "answer":
                why = await plugins.answer(str(msg.get("prompt", "")), msg.get("value"), by="skip" if msg.get("skip") else "player", who=seat)
            else:
                st = self.state
                st.active = next(i for i, inv in enumerate(st.investigators) if inv.id == seat)
                why = await plugins.command(str(msg.get("name", ""))[:20], str(msg.get("text", ""))[:200], who=seat)
            if why:
                await self._send(cid, {"t": "error", "text": why})
            self._save()
            await self._broadcast({"t": "sync", "state": self._view()})
        if any(p["deadline"] <= time.time() for p in plugins.open_prompts()):
            self.game.emit = self._event
            await plugins.expire()
            self._save()
            await self._broadcast({"t": "sync", "state": self._view()})

    async def _asks(self) -> None:
        while not self.asks.empty():
            await self._tell_again(*self.asks.get_nowait())

    async def _tell_again(self, cid: str, nudge: str, told: Told | None) -> None:
        why = "叙述已更新，请重新请求重述。" if self.game.told is not told else self.game.retell_blocked()
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
        from ..tui.library import write_save

        write_save(self.state, self.m)

    # -- serving ----------------------------------------------------------------------------
    async def serve(self, host: str = "0.0.0.0", port: int = 8765, *, linger: bool = True) -> None:
        """Listen, play the game, and keep the room open afterwards so everyone sees the end."""
        app = web.Application()
        app.router.add_get("/ws", self.ws)
        app.router.add_get("/", self._status)
        runner = web.AppRunner(app, shutdown_timeout=2.0)  # a closing room does not wait a minute for open sockets
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        try:
            await site.start()
        except OSError:  # the port is taken
            await runner.cleanup()
            raise
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
            # Every screen hears the room close at once (their link pumps end with "closed").
            await asyncio.gather(*(asyncio.wait_for(ws.close(code=WSCloseCode.GOING_AWAY, message=b"closed"), 2.0)
                                   for ws in list(self.clients.values())), return_exceptions=True)
            await self.game.close()
            await self.cast.close()
            await runner.cleanup()

    async def _status(self, request: web.Request) -> web.Response:
        body = {"module": self.m.title, "seats": self._seats(), "started": self.started.is_set(), "clock": self.state.clock}
        return web.json_response(body, dumps=lambda o: json.dumps(o, ensure_ascii=False))
