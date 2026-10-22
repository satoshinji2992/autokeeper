"""The keeper card decks (克苏鲁的呼唤守秘人卡组): read from the four PDFs once, then dealt in play.

`autokeeper cards import` reads the decks (a zip of the PDFs, a folder of them or one PDF; by
default the zip in paths.rulebooks()) into paths.keeper_decks(): decks.json, and a PNG of every
card face in faces/. The PDFs and what comes out of them stay on this machine (rule/ and the cache
are not in git).

The layout, the same in all four PDFs: A4 pages, page 1 the deck's notes, then four cards a page
in a 2 x 2 grid, each a rounded frame (a path object about 243 x 366 pt). A card's text is what
lies inside its frame; a watermark (七宫涟个人汉化) crosses the middle of every page. The
translation has no card art: a card's picture is its face.

    好事者卡  npc      ready-made people: characteristics, attacks, skills, backstory
    恐惧症卡  madness  phobias, manias, and bouts of madness (real time / summary)
    惨事卡    mishap   things that befall an investigator (five blank cards left out)
    武器卡    weapon   weapons and artifacts: skill, base chance, damage, range, rate, ammo, ...

In play every deck is optional: without imported decks nothing changes.

    madness   a bout or an indefinite insanity draws a card (Game._madness)
    mishap    the arbiter may pick one for a failed check or a bad night (Failure.mishap, Ruling.mishap)
    weapon    read and kept with the others; play does not deal it (rules/weapons.py has its own catalog)
    npc       the director may base a person it improvises on one (NewNPC.card, dress())
"""

from __future__ import annotations

import random
import re
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from . import paths

if TYPE_CHECKING:
    from .engine.state import GameState
    from .module.schema import NPC
    from .rules.coc import Investigator, Rules, Sanity

Deck = Literal["npc", "madness", "mishap", "weapon"]
DECKS: dict[str, str] = {"npc": "好事者卡", "madness": "恐惧症卡", "mishap": "惨事卡", "weapon": "武器卡"}
KINDS = {"phobia": "恐惧症", "mania": "躁狂症", "live": "疯狂发作·即时症状", "summary": "疯狂发作·总结症状"}
STORY_KEY = "恐惧症和躁狂症"  # the paper sheet's backstory entry (tui/modals/cardedit.BACKSTORY)
WATERMARK = "七宫涟个人汉化"
SCALE = 3  # the faces are rendered at 216 dpi


class Card(BaseModel):
    id: str
    deck: Deck
    name: str
    english: str = ""
    quote: str = ""
    text: str = ""  # the card's own words, paragraphs apart by blank lines
    face: str = ""  # the card's picture, relative to the decks folder
    page: int = 0  # where it was found: page (from 1) and place on the page (0-3, left to right, top first)
    slot: int = 0

    def image(self) -> Path | None:
        path = paths.keeper_decks() / self.face if self.face else None
        return path if path is not None and path.is_file() else None


class MadnessCard(Card):
    kind: Literal["phobia", "mania", "live", "summary"]
    effect: str = ""  # the rules part: what it does to checks, how long it lasts, how it is eased


class MishapCard(Card):
    summary: str = ""  # one line for a menu
    sanity: str = ""  # a SAN roll it calls for at once, "1/1D4"
    hp: str = ""  # HP lost at once
    san: str = ""  # SAN lost at once, no roll
    mp: str = ""  # MP lost at once
    luck_halved: bool = False


class WeaponCard(Card):
    subtitle: str = ""
    era: str = ""
    skill: str = ""  # as the card writes it: 射击（手枪）, 格斗（斗殴）
    base: int | None = None
    damage: str = ""
    range: str = ""
    attacks: str = ""  # per round: "1", "1(3)", "1 或 2", "1/2"
    ammo: str = ""
    malfunction: int | None = None
    price: str = ""
    special: str = ""
    used_by: str = ""  # an artifact's user (米-戈)
    fields: dict[str, str] = Field(default_factory=dict)  # every "label：value" line as written

    @property
    def impale(self) -> bool:
        return "贯穿" in self.special and "不能贯穿" not in self.special and "不可贯穿" not in self.special


class NpcCard(Card):
    occupation: str = ""
    sex: str = ""
    age: int = 0
    characteristics: dict[str, int] = Field(default_factory=dict)  # 力量 体质 体型 敏捷 外貌 智力 意志 教育
    luck: int = 0
    san: int = 0
    hp: int = 0
    move: int = 0
    db: str = ""
    build: str = ""
    dodge: int = 0
    attacks: list[str] = Field(default_factory=list)  # "切肉刀 55%，伤害 1D4+2" (the bonus is in)
    skills: dict[str, int] = Field(default_factory=dict)
    backstory: dict[str, str] = Field(default_factory=dict)  # 形象描述 思想信念 ... 秘密


class Decks(BaseModel):
    source: str = ""
    npc: list[NpcCard] = Field(default_factory=list)
    madness: list[MadnessCard] = Field(default_factory=list)
    mishap: list[MishapCard] = Field(default_factory=list)
    weapon: list[WeaponCard] = Field(default_factory=list)

    def all(self) -> list[Card]:
        return [*self.npc, *self.madness, *self.mishap, *self.weapon]

    def find(self, card_id: str) -> Card | None:
        return next((c for c in self.all() if c.id == card_id), None)


class Drawn(BaseModel):
    """A card dealt in a game (GameState.drawn): to whom, and at which turn."""

    deck: Deck
    card: str
    who: str  # investigator id
    turn: int = 0


# -- reading the PDFs ----------------------------------------------------------------------------
_SQUEEZE = re.compile(r"(?<=[^\x00-\x7f])[ 　]+(?=[^\x00-\x7f])")


def _clean(line: str) -> str:
    """One line of a card: the justification's spaces between Chinese characters gone, dashes plain."""
    line = line.replace("–", "-").replace("￾", "").replace("\x02", "").strip()
    line = re.sub(r"(?<=[^\x00-\x7f])\s*-\s*(?=[^\x00-\x7f])", "-", line.replace("㊟", "").replace("①", ""))
    return _SQUEEZE.sub("", line)


def _watermark(line: str) -> bool:
    return len(line) >= 2 and line in WATERMARK


class _Line(str):
    """A line of a card, and whether it starts a paragraph (it is indented, or centred)."""

    indent: bool = False


@dataclass
class _Face:
    deck: Deck
    page: int
    slot: int
    lines: list[_Line]
    box: tuple[float, float, float, float]  # left, bottom, right, top in PDF points


def _rows(text: Any, box: tuple[float, float, float, float]) -> list[_Line]:
    """The lines inside a card's frame, top down, from the text page's runs (a run's left edge tells
    an indented line), the watermark left out."""
    left, bottom, right, top = box
    runs = []
    for i in range(text.count_rects()):
        r = text.get_rect(i)
        if r[0] >= left and r[2] <= right + 1 and r[1] >= bottom and r[3] <= top:
            if not _watermark(_clean(text.get_text_bounded(*r))):
                runs.append(r)
    rows: list[list[float]] = []
    for r in sorted(runs, key=lambda r: -r[3]):
        row = next((x for x in rows if min(x[3], r[3]) - max(x[1], r[1]) > 0.4 * min(x[3] - x[1], r[3] - r[1])), None)
        if row is None:  # a run beside others (a smaller bracket in a title, a ～) is on their line
            rows.append(list(r))
        else:
            row[:] = [min(row[0], r[0]), min(row[1], r[1]), max(row[2], r[2]), max(row[3], r[3])]
    rows.sort(key=lambda x: -x[3])
    margin = min((r[0] for r in rows), default=left)
    out = []
    for r in rows:
        line = _Line(" ".join(x for x in (_clean(t) for t in text.get_text_bounded(*r).splitlines()) if x and not _watermark(x)))
        if line:
            line.indent = r[0] > margin + 8
            out.append(line)
    return out


def _faces(pdf_bytes: bytes, deck: Deck) -> tuple[list[_Face], Any]:
    """Every card frame of a deck with the lines inside it, and the document (for the pictures)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    faces: list[_Face] = []
    for index in range(1, len(doc)):  # page 1 is the deck's notes
        page = doc[index]
        text = page.get_textpage()
        frames = []
        for obj in page.get_objects():
            if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
                continue
            left, bottom, right, top = obj.get_bounds()
            if 200 < right - left < 300 and 300 < top - bottom < 420:
                frames.append((left, bottom, right, top))
        frames.sort(key=lambda b: (-round(b[3] / 50), b[0]))
        for slot, box in enumerate(frames):
            if lines := _rows(text, box):
                faces.append(_Face(deck, index + 1, slot, lines, box))
    return faces, doc


_NEW_PARAGRAPH = re.compile(r"^(?:•|\d+\s*[～~-]\s*\d+\s*[：:]|(?:极难成功|困难成功|成功|失败)——|选项[甲乙]|注意[：:])")


def _starts(line: str) -> bool:
    return bool(getattr(line, "indent", False) or _NEW_PARAGRAPH.match(line))


def _paragraphs(lines: list[str]) -> str:
    """Lines wrapped on the card made paragraphs again: an indented line starts one."""
    out: list[str] = []
    for line in lines:
        if out and not _starts(line):
            latin = out[-1][-1:].isascii() and out[-1][-1:].isalnum() and line[:1].isascii() and line[:1].isalnum()
            out[-1] += (" " if latin else "") + line  # words wrapped in a Latin line keep their space
        else:
            out.append(line)
    return "\n".join(p.strip() for p in out if p.strip())


def _slug(english: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", english.lower()).strip("_")


_ENGLISH = re.compile(r"^[A-Za-z][A-Za-z !?'’.\-]*$")


def _head(lines: list[str]) -> tuple[str, str, list[str]]:
    """Name and English name of a madness or mishap card, and the lines after them."""
    name, rest = lines[0], lines[1:]
    english = rest.pop(0).strip() if rest and _ENGLISH.match(rest[0]) else ""
    return name, english, rest


def _quote(rest: list[str]) -> tuple[str, list[str]]:
    """The quote under a card's name (it may wrap), and the lines after it."""
    if not rest or not rest[0].lstrip("*").startswith("“"):
        return "", rest
    taken: list[str] = []
    while rest:
        taken.append(rest.pop(0))
        if taken[-1].rstrip(" *").endswith("”") or len(taken) == 3:
            break
    return "".join(taken).strip("*"), rest


def _madness(face: _Face) -> MadnessCard:
    name, english, rest = _head(face.lines)
    kind: str = ""
    if rest and rest[0].startswith("疯狂发作"):
        kind = "live" if "即时" in rest.pop(0) else "summary"
    quote, rest = _quote(rest)
    text = _paragraphs(rest)
    if not kind:
        kind = "phobia" if english.lower().endswith("phobia") or "恐惧症" in name else "mania"
    if kind in ("phobia", "mania"):
        paras = text.split("\n")
        start = next((i for i, p in enumerate(paras) if re.search(r"惩罚骰|无法抗拒|技能检定", p)), len(paras) - 1)
        effect = "\n".join(paras[max(0, start):])
    else:
        effect = text
    return MadnessCard(id="", deck="madness", name=name, english=english, quote=quote, text=text, kind=kind,  # type: ignore[arg-type]
                       effect=effect, page=face.page, slot=face.slot)


_ROLL = r"(\d+(?:D\d+)?(?:[+-]\d+)?)"


def _mishap(face: _Face) -> MishapCard:
    name, english, rest = _head(face.lines)
    quote, rest = _quote(rest)
    text = _paragraphs(rest)
    card = MishapCard(id="", deck="mishap", name=name, english=english, quote=quote, text=text, page=face.page, slot=face.slot)
    first = text.split("\n")[0] if text else ""
    sentence = re.split(r"(?<=[。！？])", first)[0]
    card.summary = sentence if len(sentence) <= 24 else re.split(r"[，；]", sentence)[0][:24]  # a menu line
    if m := re.search(rf"理智检定\s*[(（]\s*{_ROLL}\s*/\s*{_ROLL}\s*[)）]", text):
        card.sanity = f"{m.group(1)}/{m.group(2)}"
    if m := re.search(rf"[（(]损失\s*{_ROLL}\s*点耐久值和\s*{_ROLL}\s*点理智值[）)]", text):
        card.hp, card.san = m.group(1), m.group(2)
    if m := re.search(rf"立即受到\s*{_ROLL}\s*点耐久值伤害", text):
        card.hp = m.group(1)
    if m := re.search(rf"^失去\s*{_ROLL}\s*点魔法值", text, re.M):
        card.mp = m.group(1)
    card.luck_halved = "当前幸运减半" in text
    return card


_WEAPON_LABELS = ("技能", "基础成功率", "伤害", "射程", "每轮攻击次数", "载弹量", "故障值", "价格", "特殊", "使用者",
                  "霰弹伤害", "霰弹射程", "铅弹伤害", "铅弹射程")
_LABEL = re.compile(rf"^({'|'.join(_WEAPON_LABELS)})\s*[：:]\s*(.*)$")


def _weapon(face: _Face) -> WeaponCard:
    name, rest = face.lines[0], face.lines[1:]
    card = WeaponCard(id="", deck="weapon", name=name, page=face.page, slot=face.slot)
    for _ in range(2):  # the era, and a line under the name: 稀有, （英式马刀）, MAC-11 型冲锋枪
        if not rest or _LABEL.match(rest[0]) or len(rest[0]) > 16:
            break
        line = rest.pop(0)
        if re.match(r"^(1920\s*年代|现代)", line):
            card.era = line.replace(" ", "")
        else:
            card.subtitle = line.strip("（）()")
    fields: dict[str, str] = {}
    body: list[str] = []
    label = ""
    for line in rest:
        m = _LABEL.match(line)
        if m and not body:
            label = m.group(1)
            fields[label] = m.group(2).strip()
        elif label == "特殊" and not body and not _starts(line):
            fields["特殊"] += line  # the special note goes on until the next paragraph
        else:
            body.append(line)
    card.fields = fields
    card.text = _paragraphs(body)
    card.skill = fields.get("技能", "")
    if m := re.search(r"\d+", fields.get("基础成功率", "")):
        card.base = int(m.group())
    card.damage = fields.get("伤害") or fields.get("霰弹伤害", "")
    card.range = fields.get("射程") or fields.get("霰弹射程", "")
    card.attacks = fields.get("每轮攻击次数", "")
    card.ammo = fields.get("载弹量", "")
    if m := re.search(r"\d+", fields.get("故障值", "")):
        card.malfunction = int(m.group())
    card.price = fields.get("价格", "")
    card.special = fields.get("特殊", "")
    card.used_by = fields.get("使用者", "").replace(" ", "")
    return card


_STATS = {"STR": "力量", "CON": "体质", "SIZ": "体型", "DEX": "敏捷", "APP": "外貌", "INT": "智力", "POW": "意志", "EDU": "教育"}
_STORY = ("形象描述", "思想信念", "重要之人", "意义非凡之地", "宝贵之物", "个人特质", "伤口和疤痕", "秘密")
_ATTACK = re.compile(r"^(?P<name>.+?)\s*(?P<v>\d{1,3})\s*%?\s*\(\s*\d+\s*/\s*\d+\s*\)\s*[,，]\s*伤害\s*(?P<dmg>.+?)\s*$")
_SKILL = re.compile(r"^(?P<name>.*?)\s*(?P<v>\d{1,3})\s*%?\s*\(\s*\d+\s*/\s*\d+\s*\)\s*$")


def _npc(face: _Face) -> NpcCard:
    lines = list(face.lines)
    head: list[str] = []
    while lines and not lines[0].startswith("STR"):
        head.append(lines.pop(0))
    title = _clean("".join(head))
    name, occupation = ([*re.split(r"\s*[,，]\s*", title, maxsplit=1), ""])[:2]
    card = NpcCard(id="", deck="npc", name=name.strip(), occupation=occupation.strip(" ，,"), page=face.page, slot=face.slot)
    stats = " ".join(lines[:3])
    for key, value in re.findall(r"\b(STR|CON|SIZ|DEX|APP|INT|POW|EDU)\s+(\d+)", stats):
        card.characteristics[_STATS[key]] = int(value)
    number = {k: int(v) for k, v in re.findall(r"(LUCK|SAN|HP|移动|年龄)\s+(\d+)", stats)}
    card.luck, card.san, card.hp, card.move, card.age = (number.get(k, 0) for k in ("LUCK", "SAN", "HP", "移动", "年龄"))
    card.db = m.group(1) if (m := re.search(r"DB\s+([+\-]?\w+)", stats)) else ""
    card.build = m.group(1) if (m := re.search(r"体格\s+([+\-]?\d+)", stats)) else ""
    lines = lines[3:]
    # dodge and attacks, until the skills
    while lines and not re.match(r"^技能\s*[：:]", lines[0]):
        line = lines.pop(0)
        while lines and not _ATTACK.match(line) and not re.match(r"^闪避", line) and not re.match(r"^技能\s*[：:]", lines[0]):
            line += lines.pop(0)  # an attack that wrapped
        if m := re.match(r"^闪避\s*(\d+)", line):
            card.dodge = int(m.group(1))
        elif m := _ATTACK.match(line):
            card.attacks.append(f"{m.group('name').strip()} {m.group('v')}%，伤害 {m.group('dmg').replace(' ', '')}")
    # skills, then the backstory
    skills = ""
    while lines and not re.match(rf"^({'|'.join(_STORY)})\s*[：:]", lines[0]):
        skills += lines.pop(0)
    skills = re.sub(r"^技能\s*[：:]", "", skills).rstrip("。.")
    base = ""
    for part in re.split(r"[，,](?![^（(]*[）)])", skills):
        m = _SKILL.match(part.strip())
        if not m:
            continue
        named = m.group("name").strip()
        if named.startswith(("（", "(")) and base:
            named = base + named
        else:
            base = re.split(r"[（(]", named)[0]
        card.skills[named.replace("（", "(").replace("）", ")").replace(" ", "")] = int(m.group("v"))
    label = ""
    for line in lines:
        if m := re.match(rf"^({'|'.join(_STORY)})\s*[：:]\s*(.*)$", line):
            label = m.group(1)
            card.backstory[label] = m.group(2)
        elif label:
            card.backstory[label] += line
    words = "".join(card.backstory.values())
    card.sex = "女" if "女" in card.occupation or words.count("她") > words.count("他") else "男"
    card.text = "\n".join(f"{k}：{v}" for k, v in card.backstory.items())
    return card


def _deck_of(name: str, first_page: str) -> Deck | None:
    """Which deck a PDF is: by its file name, else by the title on its first page."""
    words = {"npc": "好事者", "madness": "恐惧症", "mishap": "惨事", "weapon": "武器"}
    for text in (name, first_page[:60]):
        for deck, word in words.items():
            if word in text:
                return deck  # type: ignore[return-value]
    return None


def _sources(path: Path) -> Iterable[tuple[str, bytes]]:
    """(file name, bytes) of every PDF in a zip, a folder or one file."""
    if path.is_dir():
        for p in sorted(path.glob("*.pdf")):
            yield p.name, p.read_bytes()
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                if not info.flag_bits & 0x800:  # a name a Chinese Windows zipped, read as cp437
                    try:
                        raw = name.encode("cp437")
                    except UnicodeEncodeError:  # zipfile read it right already (a Unicode path field)
                        raw = b""
                    for enc in ("utf-8", "gbk") if raw else ():
                        try:
                            name = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                if name.lower().endswith(".pdf"):
                    yield Path(name).name, z.read(info)
    elif path.suffix.lower() == ".pdf":
        yield path.name, path.read_bytes()


def default_source() -> Path | None:
    """The decks in the rulebooks folder: a zip or PDFs whose names say 守秘人卡组, else any zip there."""
    folder = paths.rulebooks()
    if not folder.is_dir():
        return None
    found = sorted(p for p in folder.iterdir() if "卡组" in p.name and p.suffix.lower() in (".zip", ".pdf"))
    zips = [p for p in found if p.suffix.lower() == ".zip"]
    if zips:
        return zips[0]
    return folder if found else None


def read(source: Path, out: Path | None = None, *, faces: bool = True) -> Decks:
    """Every card of the decks at `source`, with a picture of each face in `out`/faces/ (`faces`)."""
    import pypdfium2 as pdfium

    out = out or paths.keeper_decks()
    decks = Decks(source=str(source))
    for name, data in _sources(source):
        try:
            first = pdfium.PdfDocument(data)[0].get_textpage().get_text_range()[:400]
        except Exception:  # not a PDF that opens: not a deck
            continue
        deck = _deck_of(name, first)
        if deck is None:
            continue
        found, keep = _faces(data, deck)
        cards: list[Card] = []
        for face in found:
            if deck == "madness":
                card: Card = _madness(face)
            elif deck == "mishap":
                card = _mishap(face)
            elif deck == "weapon":
                card = _weapon(face)
            else:
                card = _npc(face)
            cards.append(card)
        taken: set[str] = set()
        for n, card in enumerate(cards, 1):
            ident = _slug(card.english) if card.english else f"{deck}{n:02d}"
            if isinstance(card, MadnessCard) and ident in taken:
                ident = f"{ident}_{card.kind}"
            while ident in taken:
                ident += "_2"
            taken.add(ident)
            card.id = ident
            card.face = f"faces/{deck}/{ident}.png"
        if faces:
            _render(keep, found, cards, out)
        getattr(decks, deck).extend(cards)
    return decks


def _render(doc: Any, found: list[_Face], cards: list[Card], out: Path) -> None:
    pages: dict[int, Any] = {}
    for face, card in zip(found, cards, strict=True):
        if face.page not in pages:
            pages.clear()
            pages[face.page] = doc[face.page - 1].render(scale=SCALE).to_pil()
        image = pages[face.page]
        height = image.height / SCALE
        left, bottom, right, top = face.box
        box = (int(left * SCALE), int((height - top) * SCALE), int(right * SCALE), int((height - bottom) * SCALE))
        target = out / card.face
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop(box).save(target)


def save(decks: Decks, out: Path | None = None) -> Path:
    out = out or paths.keeper_decks()
    out.mkdir(parents=True, exist_ok=True)
    target = out / "decks.json"
    part = target.with_suffix(".json.tmp")
    part.write_text(decks.model_dump_json(indent=1), encoding="utf-8")
    part.replace(target)
    _load.cache_clear()
    return target


def coverage(decks: Decks) -> dict[str, dict[str, int]]:
    """How much of each deck was read: cards, and how many have each part that deck should have."""
    def count(cards: list, *checks) -> dict[str, int]:
        return {"cards": len(cards), **{name: sum(1 for c in cards if test(c)) for name, test in checks}}

    return {
        "npc": count(decks.npc, ("职业", lambda c: c.occupation), ("八项属性", lambda c: len(c.characteristics) == 8),
                     ("生命", lambda c: c.hp), ("年龄", lambda c: c.age), ("闪避", lambda c: c.dodge),
                     ("攻击", lambda c: c.attacks), ("技能", lambda c: len(c.skills) >= 5), ("背景", lambda c: len(c.backstory) >= 5)),
        "madness": count(decks.madness, ("英文名", lambda c: c.english), ("引言", lambda c: c.quote), ("效果", lambda c: c.effect)),
        "mishap": count(decks.mishap, ("英文名", lambda c: c.english), ("正文", lambda c: c.text), ("即时结算", lambda c: c.sanity or c.hp or c.san or c.mp or c.luck_halved)),
        "weapon": count(decks.weapon, ("技能", lambda c: c.skill), ("基础成功率", lambda c: c.base is not None),
                        ("伤害", lambda c: c.damage), ("射程", lambda c: c.range), ("每轮次数", lambda c: c.attacks),
                        ("价格", lambda c: c.price), ("器物", lambda c: c.used_by)),
    }


# -- the imported decks ------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _load(file: str, stamp: float) -> Decks | None:
    try:
        return Decks.model_validate_json(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load() -> Decks | None:
    """The imported decks, or None: then the game plays without them."""
    file = paths.keeper_decks() / "decks.json"
    try:
        stamp = file.stat().st_mtime
    except OSError:
        return None
    return _load(str(file), stamp)



def find(card_id: str) -> Card | None:
    decks = load()
    return decks.find(card_id) if decks else None


def view(card: Card, who: str = "") -> dict[str, Any]:
    """A card as the screens show it (tui/modals/deck.py): its words, and the picture's path."""
    image = card.image()
    kind = KINDS.get(getattr(card, "kind", ""), "") or DECKS[card.deck]
    return {"deck": card.deck, "id": card.id, "kind": kind, "name": card.name, "english": card.english, "quote": card.quote,
            "text": card.text, "image": str(image) if image else "", "who": who}


# -- madness -----------------------------------------------------------------------------------
def held(inv: Investigator) -> list[str]:
    """The phobias and manias on an investigator's sheet."""
    return [x for x in re.split(r"[、，,；;\n]", inv.backstory.get(STORY_KEY, "")) if x.strip()]


def _record(inv: Investigator, card: MadnessCard) -> None:
    have = held(inv)
    if card.name not in have:
        inv.backstory[STORY_KEY] = "、".join([*have, card.name])


def for_sanity(state: GameState, inv: Investigator, s: Sanity, rng: random.Random | None = None) -> MadnessCard | None:
    """The card a SAN roll's madness draws, put on the sheet and in the game's record; None without
    decks or madness. An indefinite insanity brings a phobia or a mania; a bout of temporary madness
    a bout card (real time when another investigator is there to see it, else summary), or, on a
    9 or 10 of the bout's D10 (Keeper Rulebook p. 157), a phobia or a mania."""
    decks = load()
    if decks is None or s.permanent or not (s.temporary or s.indefinite) or not decks.madness:
        return None
    rng = rng or random.Random()
    if s.indefinite:
        kind = rng.choice(("phobia", "mania"))
    else:
        roll = rng.randint(1, 10)
        others = any(o.id != inv.id and o.can_act for o in state.investigators)
        kind = "phobia" if roll == 9 else "mania" if roll == 10 else "live" if others else "summary"
    have = set(held(inv))
    pool = [c for c in decks.madness if c.kind == kind and c.name not in have] or [c for c in decks.madness if c.kind == kind]
    if not pool:
        return None
    card = rng.choice(pool)
    if card.kind in ("phobia", "mania"):
        _record(inv, card)
    _deal(state, "madness", card.id, inv.id)
    return card


def madness_note(inv: Investigator, card: MadnessCard) -> str:
    """What the keeper is told when the card is drawn."""
    kind = KINDS[card.kind]
    lasting = "记在角色卡上，之后一直有效。" if card.kind in ("phobia", "mania") else "以这张卡代替疯狂发作表的结果。"
    return f"{inv.name}抽到{kind}卡〔{card.name}〕，{lasting}照卡上的效果演出与裁定：{card.effect or card.text}"


def _deal(state: GameState, deck: Deck, card_id: str, who: str) -> None:
    drawn = getattr(state, "drawn", None)
    if isinstance(drawn, list):
        drawn.append(Drawn(deck=deck, card=card_id, who=who, turn=state.turn))


# -- mishaps -----------------------------------------------------------------------------------
def mishap_menu(limit: int = 60) -> str:
    """The mishap deck as the arbiter picks from it: id, name, one line."""
    decks = load()
    if decks is None or not decks.mishap:
        return ""
    rows = [f"- {c.id}〔{c.name}〕{c.summary}" for c in decks.mishap[:limit]]
    return ("【惨事卡（可选）】孤注一掷再失败（on_push_fail）、检定失败（on_fail）或休息过夜（mishap）出了岔子时，"
            "可以在对应的 mishap 填一张卡的 id：引擎结算卡上的即时效果，把全文交给守秘人。与这次行动和场景相称才用，多数时候留空。\n"
            + "\n".join(rows))


@dataclass
class Mishap:
    card: MishapCard
    lines: list[str]  # what was settled at once, for the keeper and the screen
    sanity: Sanity | None = None


def deal_mishap(state: GameState, inv: Investigator, card_id: str, rules: Rules, *, hp: bool = True) -> Mishap | None:
    """A mishap card dealt to an investigator: its immediate effects settled by the rules (a SAN roll,
    HP, SAN or MP lost, Luck halved), the card recorded as holding (GameState.drawn). None when there
    is no such card. `hp` False: its HP loss is the caller's to land, as every wound is, through
    engine/effects with an injury id (Game._mishap)."""
    card = find(card_id.strip())
    if not isinstance(card, MishapCard):
        return None
    lines: list[str] = []
    if card.hp and hp:
        hurt = rules.damage(inv, card.hp)
        lines.append(f"{inv.name}{hurt.describe()}")
    if card.san:
        loss = rules.dice.total(card.san) if "D" in card.san else int(card.san)
        before = inv.san
        inv.san = max(0, inv.san - loss)
        inv.san_lost_today += loss
        lines.append(f"{inv.name}理智 {before} → {inv.san}")
    if card.mp:
        loss = rules.dice.total(card.mp) if "D" in card.mp else int(card.mp)
        before = inv.mp
        inv.mp = max(0, inv.mp - loss)
        lines.append(f"{inv.name}魔法 {before} → {inv.mp}")
    if card.luck_halved:
        before = inv.luck
        inv.luck //= 2
        lines.append(f"{inv.name}幸运 {before} → {inv.luck}")
    sanity = None
    if card.sanity:
        ok, bad = card.sanity.split("/")
        sanity = rules.sanity(inv, ok, bad)
        lines.append(f"{inv.name}{sanity.describe()}")
    _deal(state, "mishap", card.id, inv.id)
    return Mishap(card, lines, sanity)


def mishap_note(inv: Investigator, dealt: Mishap) -> str:
    done = f"已结算：{'；'.join(dealt.lines)}。" if dealt.lines else ""
    return f"惨事卡〔{dealt.card.name}〕落在{inv.name}身上。{done}卡面全文（其余效果由守秘人照此演出）：{dealt.card.text}"


# -- what holds on an investigator, for the arbiter ----------------------------------------------
def standing(state: GameState, inv: Investigator) -> str:
    """The phobias and manias on this investigator's sheet, with what they do to checks. A mishap is
    not among them: what it does at once the engine settles (deal_mishap), the rest of its card the
    keeper plays out when it is dealt (mishap_note). Nothing marks when a card's 「直到……」 is over,
    so the arbiter is not handed it every turn for the rest of the game (the review of 2026-10-22)."""
    decks = load()
    if decks is None:
        return ""
    rows: list[str] = []
    for name in held(inv):
        card = next((c for c in decks.madness if c.name == name), None)
        rows.append(f"- {name}：{card.effect if card else '（无卡面）'}")
    return f"【{inv.name}身上的恐惧症与躁狂症】按卡面裁定（惩罚骰、难度）：\n" + "\n".join(rows) if rows else ""


# -- ready-made people --------------------------------------------------------------------------
def npc_menu(text: str = "", limit: int = 8) -> str:
    """A few people from the 好事者 deck for the director: those whose occupation the action's words
    name first, then others; id, name, occupation, age, sex."""
    decks = load()
    if decks is None or not decks.npc:
        return ""
    def score(c: NpcCard) -> int:
        words = {c.occupation, *re.split(r"[、，,]", c.occupation)}
        return sum(len(w) for w in words if len(w) >= 2 and w in text)

    picked = sorted(decks.npc, key=lambda c: (-score(c), zlib.crc32(f"{c.id}{text}".encode())))[:limit]
    rows = [f"- {c.id} {c.name}，{c.occupation}，{c.age} 岁，{c.sex}" for c in picked]
    return ("【好事者卡（可选）】即兴的新人物合适时可以直接用一张：在 npcs 的 card 填卡 id，人物会得到卡上的属性、技能与攻击；"
            "名字、身份、外貌可以按本局改写。\n" + "\n".join(rows))


def dress(npc: NPC, card_id: str) -> bool:
    """A director's new NPC given a 好事者 card's numbers: characteristics, HP, MOV, dodge, skills and
    attacks; its look and personality from the card where the director left them empty."""
    card = find(card_id.strip()) if card_id else None
    if not isinstance(card, NpcCard):
        return False
    stats = dict(card.characteristics)
    stats |= {"生命": card.hp, "移动": card.move, "闪避": card.dodge, "幸运": card.luck, "理智": card.san}
    for skill, value in card.skills.items():
        stats.setdefault(skill, value)
    for line in card.attacks:
        if m := re.match(r"^(斗殴)\s+(\d+)%", line):
            stats["格斗(斗殴)"] = int(m.group(2))
    npc.stats = {k: v for k, v in stats.items() if v} | npc.stats
    npc.attacks = npc.attacks or [a for a in card.attacks if not a.startswith("斗殴")]
    if not npc.look:
        npc.look = card.backstory.get("形象描述", "")
    if not npc.personality:
        npc.personality = "；".join(x for x in (card.backstory.get("思想信念", ""), card.backstory.get("个人特质", "")) if x)
    return True


# -- the command line ------------------------------------------------------------------------
def summary(decks: Decks) -> list[str]:
    lines = []
    for deck, parts in coverage(decks).items():
        n = parts.pop("cards")
        lines.append(f"{DECKS[deck]}：{n} 张" + ("（" + "，".join(f"{k} {v}/{n}" for k, v in parts.items()) + "）" if n else ""))
    return lines



def arbiter_context(state: GameState, inv: Investigator) -> str:
    """For the arbiter's prompt: what holds on the investigator, and the mishaps it may deal; empty
    without imported decks."""
    parts = [x for x in (standing(state, inv), mishap_menu()) if x]
    return "".join(f"\n{x}" for x in parts)
