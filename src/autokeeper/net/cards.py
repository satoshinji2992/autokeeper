"""Investigators players bring to a LAN table, and cards made there on the host's models.

A card that comes over the wire is checked before it sits at the table (vet): its shape,
characteristics and skills in the 7th edition's ranges, its text cut to size; HP, MP and the
SAN ceiling are worked out again by the rules code, never taken from the card.

A friend often has no model of their own, so 车卡 for a seated player runs on the host: the
player's screen is the ordinary card screen (tui/screens/card.py), its Builder a RemoteBuilder that
sends each step to the Hub, where a real Builder does it (step). The messages:

    client -> hub   card {op: begin | look_up | read | propose | build | find | photo | cancel | end, n, ...}
    hub -> client   card {n, figure? card? fixes? note? kind? proposal? data? error?}
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import credits, roster
from ..agents.card import Builder, CardError, Figure, Photo, Proposal, download, local_file
from ..module.schema import Module
from ..rules.catalog import CHARACTERISTICS, canonical
from ..rules.coc import Investigator
from . import media

CARD_BYTES = 48_000  # a whole card as JSON
FILE_BYTES = 4_000_000  # a file brought to 导入已有的卡
FILE_KINDS = {".png", ".webp", ".jpg", ".jpeg", ".json", ".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx", ".xls", ".html", ".htm"}
PHOTOS = 8  # photo candidates the host fetches for one player
MODERN = ("计算机使用", "电子学")
MODERN_THINGS = ("手机", "电脑", "笔记本电脑", "平板", "U盘", "耳机", "充电宝")


class Refused(ValueError):
    """A brought card the table cannot take, in one plain sentence."""


def _text(value: Any, size: int) -> str:
    return value.strip()[:size] if isinstance(value, str) else ""


def _number(value: Any, low: int, high: int, why: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refused(why)
    return value


def vet(data: Any, inv_id: str) -> Investigator:
    """The card as the table takes it, under `inv_id`; Refused when it cannot be played."""
    if not isinstance(data, dict) or len(json.dumps(data, ensure_ascii=False, default=str)) > CARD_BYTES:
        raise Refused("这张卡读不了")
    name = _text(data.get("name"), 24)
    if not name:
        raise Refused("这张卡没有名字")
    raw = data.get("characteristics")
    if not isinstance(raw, dict) or {canonical(str(k)) for k in raw} != set(CHARACTERISTICS):
        raise Refused("这张卡的八项属性不全")
    stats = {canonical(str(k)): _number(v, 1, 99, "属性要在 1 到 99 之间") for k, v in raw.items()}
    luck = _number(data.get("luck"), 1, 99, "幸运要在 1 到 99 之间")
    age = _number(data.get("age", 30), 15, 89, "年龄要在 15 到 89 岁之间")
    skills: dict[str, int] = {}
    given = data.get("skills") or {}
    if not isinstance(given, dict) or len(given) > 80:
        raise Refused("这张卡的技能读不了")
    for k, v in given.items():
        key = canonical(_text(k, 24))
        value = _number(v, 0, 99, "技能要在 0 到 99 之间")
        if key and key not in CHARACTERISTICS and key not in ("理智", "幸运", "灵感", "知识"):
            skills[key] = value
    conditions = data.get("conditions") or []
    if isinstance(conditions, list) and {"死亡", "永久疯狂"} & set(conditions):
        raise Refused("这位调查员已经不能上场了")
    story = data.get("backstory") or {}
    story = {_text(k, 12): _text(v, 400) for k, v in list(story.items())[:12]} if isinstance(story, dict) else {}
    things = data.get("inventory") or []
    things = [_text(x, 40) for x in things[:20]] if isinstance(things, list) else []
    inv = Investigator(
        id=inv_id, name=name, occupation=_text(data.get("occupation"), 40), age=age, sex=_text(data.get("sex"), 8),
        residence=_text(data.get("residence"), 40), birthplace=_text(data.get("birthplace"), 40),
        backstory={k: v for k, v in story.items() if k and v}, characteristics=stats, luck=luck, skills=skills,
        inventory=[x for x in things if x],
    )  # fmt: skip  (HP, MP and SAN from the characteristics, as a new sheet has them)
    for pool, top in (("hp", inv.hp_max), ("mp", inv.mp_max), ("san", inv.san_max)):
        now = data.get(pool)
        if isinstance(now, int) and not isinstance(now, bool) and 0 < now <= top:  # an old wound, sanity already spent
            setattr(inv, pool, now)
        else:
            setattr(inv, pool, min(getattr(inv, pool), top))
    inv.san_start_of_day = inv.san
    return inv


def era_note(inv: Investigator, m: Module) -> str:
    """A word for the host when the card looks like it is from another time than the module."""
    years = [int(y) for y in re.findall(r"1[6-9]\d\d", m.era)]
    modern = any(s.split("(")[0] in MODERN for s in inv.skills) or any(w in x for x in inv.inventory for w in MODERN_THINGS)
    return "像是现代的卡，和模组年代不一样" if years and min(years) < 1960 and modern else ""


# -- on the host -----------------------------------------------------------------------------
def figure_out(fig: Figure | None) -> dict | None:
    """The figure for the player's screen: web photos keep their address; a file on the host
    (the picture of an uploaded card) is only a number, fetched through the Hub."""
    if fig is None:
        return None
    d = asdict(fig)
    for i, p in enumerate(d["photos"]):
        if not str(p["url"]).startswith(("http://", "https://")):
            p["url"] = f"upload:{i}"
    return d


def figure_in(d: Any) -> Figure | None:
    if not isinstance(d, dict):
        return None
    photos = [Photo(**p) for p in d.get("photos", []) if isinstance(p, dict)]
    return Figure(**{k: v for k, v in d.items() if k in Figure.__dataclass_fields__ and k != "photos"}, photos=photos)


def _card(b: Builder) -> dict:
    return {"card": b.card.model_dump(mode="json") if b.card else None, "fixes": b.fixes, "note": b.note, "kind": b.kind}


async def step(b: Builder, op: str, msg: dict, work: Path) -> dict:
    """One step of a remote player's 车卡, on the host's Builder; what goes back to them."""
    if op == "look_up":
        return {"figure": figure_out(await b.look_up(_text(msg.get("query"), 200)))}
    if op == "find":
        return {"figure": figure_out(await b.find(_text(msg.get("query"), 200)))}
    if op == "read":
        if msg.get("file"):
            suffix = Path(_text(msg.get("name"), 120)).suffix.lower()
            if suffix not in FILE_KINDS:
                raise CardError("这种文件读不了，换成图片、表格或文字")
            try:
                data = base64.b64decode(str(msg["file"]), validate=True)
            except (ValueError, binascii.Error):
                raise CardError("文件没传过来，再试一次") from None
            if len(data) > FILE_BYTES:
                raise CardError("文件太大了，换一个小一点的")
            work.mkdir(parents=True, exist_ok=True)
            path = work / f"upload{suffix}"
            path.write_bytes(data)
            await b.read(str(path))  # a file the Hub wrote itself
        else:
            await b.read(_text(msg.get("text"), 12000), files=False)  # never a path on the host
        return {"figure": figure_out(b.figure)} | _card(b)
    if op == "propose":
        return {"proposal": (await b.propose()).model_dump()}
    if op == "build":
        answers = msg.get("answers") or []
        b.answers = [(_text(q, 60), _text(a, 60)) for q, a in answers[:6]] if isinstance(answers, list) else []
        await b.build(_text(msg.get("request"), 300))
        return _card(b)
    if op == "photo":
        i = msg.get("i")
        if not isinstance(i, int) or not 0 <= i < PHOTOS:
            raise CardError("这张照片拿不到")
        dest = work / f"photo-{i}.jpg"
        if not dest.exists() and not await b.download(i, dest):
            return {"data": ""}
        return {"data": await asyncio.to_thread(media.pack, str(dest), max_size=800)}
    raise CardError("看不懂这一步")


# -- on the player's screen --------------------------------------------------------------------
class RemoteBuilder:
    """The Builder the card screen talks to at a LAN table: each step is done on the host.
    `save`: keep the finished card in this machine's 我的角色 (not in a browser seat, which
    runs on the host's machine); `files`: files typed or dragged in are this machine's own."""

    def __init__(self, link: Any, m: Module, *, save: bool = True, files: bool = True, notify: Any = None):
        self.link, self.m, self.save, self.files = link, m, save, files
        self.notify = notify or (lambda text: None)
        self.id = f"card-{uuid.uuid4().hex[:6]}"
        self.figure: Figure | None = None
        self.photo: Photo | None = None
        self.answers: list[tuple[str, str]] = []
        self.requests: list[str] = []
        self.card: Investigator | None = None
        self.fixes: list[str] = []
        self.note = ""
        self.kind = "figure"
        self.portrait_data = ""  # the photo picked, packed, to go with the card to the table
        self._n = 0
        self._due: dict[int, asyncio.Future] = {}
        self._begun = False

    async def _call(self, op: str, **fields: Any) -> dict:
        if not self._begun:
            self._begun = True
            await self.link.send({"t": "card", "op": "begin"})
        self._n += 1
        n, due = self._n, asyncio.get_running_loop().create_future()
        self._due[n] = due
        try:
            await self.link.send({"t": "card", "op": op, "n": n, **fields})
            reply = await due
        except asyncio.CancelledError:  # Esc on the card screen: the host stops too
            await asyncio.shield(self.link.send({"t": "card", "op": "cancel"}))
            raise
        finally:
            self._due.pop(n, None)
        if reply.get("error"):
            raise CardError(reply["error"])
        return reply

    def reply(self, msg: dict) -> None:
        """What the Hub answered; a closed room answers everything that waits."""
        if msg.get("t") == "closed":
            for due in self._due.values():
                if not due.done():
                    due.set_result({"error": "和房主的连接断了"})
            return
        due = self._due.get(msg.get("n", -1))
        if due is not None and not due.done():
            due.set_result(msg)

    async def end(self) -> None:
        if self._begun:
            await self.link.send({"t": "card", "op": "end"})

    def _take(self, reply: dict) -> None:
        if "figure" in reply:
            self.figure = figure_in(reply["figure"])
        if reply.get("card"):
            self.card = Investigator.model_validate(reply["card"])
            self.fixes, self.note, self.kind = list(reply.get("fixes") or []), str(reply.get("note") or ""), reply.get("kind", self.kind)

    async def look_up(self, query: str) -> Figure:
        try:
            self._take(await self._call("look_up", query=query))
        except CardError as exc:  # nothing found: the questions go on from the name alone
            self.notify(str(exc))
            self.figure = Figure(query.strip())
        assert self.figure
        return self.figure

    async def find(self, query: str) -> Figure:
        try:
            self._take(await self._call("find", query=query))
        except CardError as exc:
            self.notify(str(exc))
            self.figure = Figure(query.strip())
        assert self.figure
        return self.figure

    async def read(self, source: str) -> str:
        path = local_file(source) if self.files else None
        if path is not None:
            if path.stat().st_size > FILE_BYTES:
                raise CardError("文件太大了，换一个小一点的")
            data = base64.b64encode(await asyncio.to_thread(path.read_bytes)).decode("ascii")
            reply = await self._call("read", file=data, name=path.name)
        else:
            reply = await self._call("read", text=source)
        self._take(reply)
        return "sheet" if self.kind == "sheet" and self.card else "persona"

    async def propose(self) -> Proposal:
        return Proposal.model_validate((await self._call("propose"))["proposal"])

    async def build(self, request: str = "") -> Investigator:
        if request:
            self.requests.append(request)
        self._take(await self._call("build", answers=[list(qa) for qa in self.answers], request=request))
        assert self.card
        return self.card

    async def download(self, i: int, dest: Path) -> bool:
        if not self.figure or not 0 <= i < len(self.figure.photos):
            return False
        photo = self.figure.photos[i]
        if not photo.url.startswith(("http://", "https://", "upload:")):  # one the player picked on this machine
            return self.files and await download(photo, dest)
        try:
            data = media.picture((await self._call("photo", i=i)).get("data", ""), max_size=800)
        except CardError:
            return False
        if not data:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        credits.put(dest, asdict(photo) | {"kind": "portrait"})
        return True

    def keep(self, photo: Path | None) -> None:
        assert self.card
        if photo is not None:
            self.portrait_data = media.pack(str(photo))
        if self.save:
            roster.keep(self.card, self.record(), photo)

    def record(self) -> dict:
        fig = self.figure or Figure("")
        source = {"query": fig.query, "title": fig.title, "page": fig.page, "photo": asdict(self.photo) if self.photo else None}
        return {"figure": source, "answers": self.answers, "requests": self.requests, "module": self.m.id, "era": self.m.era, "made": "联机车卡"}
