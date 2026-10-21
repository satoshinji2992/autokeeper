"""Character creation: from a public figure, or from a card the player already has.

    a name  ->  what the web knows: Wikipedia (Chinese, Japanese, English) when there is a
                page by that name, web search and the top pages otherwise; photos from
                Wikipedia, image search and Wikimedia Commons, for the player to pick
            ->  three questions that settle what kind of investigator the player wants
            ->  an investigator for this module's era and place
            ->  changes asked for in the player's own words

The model decides who the person is in this story and what they are good at; the
creation rules (characteristics array, skill points, base chances) are applied in code
by rules/creation.py. Offline, the model works from what it already knows.

A card the player brings is read, not rebuilt: a dice bot's .st line is parsed by code,
a sheet in a document or a photo is copied by a model, and only impossible values are
corrected (rules/creation.accept). A character without numbers — a SillyTavern card, a
description — goes through the questions like a public figure, its picture as the photo.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import io
import json
import random
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image
from pydantic import BaseModel, Field

from .. import credits, imagery
from ..config import Config
from ..harness import Session, search
from ..harness.search import resolve
from ..module import sources
from ..module.schema import Module
from ..rules.catalog import CHARACTERISTICS, SKILL_BASE, canonical
from ..rules.coc import Investigator
from ..rules.creation import FORMULAS, accept, make
from .writing import INTERFACE_COPY

RESPECT = "尊重本人：只用公开的身份和经历，写正面或中性的内容；不编造丑闻、罪行和私生活，不写住址和家人等隐私，不评价政治立场。"


class CardError(Exception):
    """A step that did not work, already said in one plain sentence (a card made on the
    host's models at a LAN table, net/cards.py)."""


@dataclass
class Photo:
    url: str
    page: str = ""  # where it was found
    title: str = ""
    license: str = ""


@dataclass
class Figure:
    query: str
    title: str = ""  # Wikipedia page, when there is one by that name
    description: str = ""
    extract: str = ""
    page: str = ""
    english: str = ""  # English name, for photos on Commons
    web: list[str] = field(default_factory=list)  # search snippets and page excerpts, with their sources
    photos: list[Photo] = field(default_factory=list)
    source: str = "维基百科"  # where `extract` comes from: Wikipedia, or the player's character card

    @property
    def name(self) -> str:
        return self.title or self.query

    def brief(self, limit: int = 4000) -> str:
        if self.source == "描述":  # the player's own idea of a character: nothing to look up
            return f"玩家想要的调查员：{self.extract}"
        about = f"{self.description}。" if self.description else ""
        parts = [f"{self.source}《{self.name}》：{about}{self.extract}"] if self.extract else []
        text = "\n".join(parts + self.web)[:limit]
        return text or f"{self.query}（没有查到资料，按你知道的来）"


class Question(BaseModel):
    ask: str = Field(description="背景确认问题，直接询问一项内容，简洁正式，不超过 20 字")
    options: list[str] = Field(description="2–4 个含义明确、结构平行的选项，每个不超过 24 字")


class Proposal(BaseModel):
    who: str = Field(description="玩家可见的人物简介，客观说明身份与经历，不评价、不使用宣传语，不超过 60 字")
    photo: str = Field("", description="搜这个人照片用的关键词：名字加上能把他和同名的人、作品区分开的两三个词，比如“哈姆 阿拉伯语 幼教动画”；描述出来的人物留空")
    questions: list[Question] = Field(description="三个问题")


class Stats(BaseModel):
    力量: int
    体质: int
    体型: int
    敏捷: int
    外貌: int
    智力: int
    意志: int
    教育: int


class Backstory(BaseModel):
    个人描述: str
    思想与信念: str
    重要之人: str
    意义非凡之地: str
    宝贵之物: str
    特质: str


class Draft(BaseModel):
    name: str = Field(description="名字：本人的名字，或改编后符合模组年代和地点的化名")
    occupation: str = Field(description="职业，符合模组的年代和地点，可以加括号说明，如“作家（《晨报》副刊撰稿人）”")
    age: int
    sex: str = ""
    residence: str = ""
    birthplace: str = ""
    characteristics: Stats = Field(description="把 80、70、60、60、50、50、50、40 分给八项属性，每个数用一次")
    formula: Literal["教育×4", "教育×2+敏捷×2", "教育×2+力量×2", "教育×2+外貌×2", "教育×2+意志×2"] = Field(description="职业技能点的算法")
    credit: int = Field(description="信用评级，从职业技能点里出")
    occupation_skills: dict[str, int] = Field(description="职业技能在基础值之上加的点，八项左右，不含信用评级")
    interest_skills: dict[str, int] = Field(description="兴趣技能在基础值之上加的点，合计不超过 智力×2")
    backstory: Backstory
    inventory: list[str] = Field(default_factory=list, description="随身物品三五件，符合年代")
    note: str = Field("", description="玩家可见的配置说明，客观说明身份或技能侧重，不使用口号或人物评语，不超过 40 字")


class Sheet(BaseModel):
    """A card made elsewhere, copied as written."""

    name: str = ""
    occupation: str = ""
    age: int = 0
    sex: str = ""
    residence: str = ""
    birthplace: str = ""
    characteristics: Stats = Field(description="八项属性，原卡写多少填多少，没写的填 0")
    # A 0 the card writes stands (luck spent, sanity gone); one it does not write is None (rules/creation.accept).
    luck: int | None = Field(None, description="幸运，原卡写多少填多少，写的是 0 也填 0；没写填 null")
    san: int | None = Field(None, description="当前理智，原卡写多少填多少，写的是 0 也填 0；没写填 null")
    hp: int | None = Field(None, description="当前 HP，原卡写多少填多少，写的是 0 也填 0；没写填 null")
    skills: dict[str, int] = Field(default_factory=dict, description="技能的最终值，原卡写多少填多少")
    backstory: dict[str, str] = Field(default_factory=dict, description="背景各项（个人描述、思想与信念、重要之人、意义非凡之地、宝贵之物、特质等），原卡有什么写什么")
    inventory: list[str] = Field(default_factory=list)
    note: str = Field("", description="玩家可见的导入说明，简洁正式地说明缺失信息及采用的假设")


READ_SYSTEM = """你把玩家已经做好的《克苏鲁的呼唤》第 7 版人物卡录进游戏。原卡可能是骰娘的 .st 指令、Excel 或网页车卡器导出的表、PDF 人物卡、纸质卡的照片，或者一段文字。

- 原卡写了什么数值就填什么：不改、不补、不重新分配。属性填八项中文名，技能填最终值（不是加点），只填比基础值高的。
- 技能名换成这些标准名（括号里是专长，比如 艺术与手艺(摄影)、外语(英语)）：{skills}；这里没有的技能照原名保留。
- 原卡没写的文字项（姓名、职业、年龄、背景）留空，不要编；在 note 里说缺了什么。
- 原卡根本不是人物卡（没有属性数值，只是一段人设）时，八项属性都填 0，把人设里的姓名和背景照填。""" + "\n\n" + INTERFACE_COPY

REVISE_SYSTEM = """你在帮玩家改一张已经录进游戏的《克苏鲁的呼唤》第 7 版人物卡。按玩家的要求改，没提到的地方原样保留，输出改好的整张卡。
属性在 1–99，技能最终值不超过 99。note 里一句话说改了什么。""" + "\n\n" + INTERFACE_COPY

ASK_SYSTEM = f"""你在帮玩家把一位公众人物、一张角色卡里的人物，或者玩家自己的一句描述，做成《克苏鲁的呼唤》第 7 版的调查员。先根据资料出三个问题，弄清玩家想要什么样的角色。

- who：一句话介绍这个人，不超过 60 字。
- 第一个问题：在这个模组的年代和地点，他是什么身份。给 3 个选项：一个尽量贴近本人（同一个人，或者本人在那个年代最可能的样子），两个是有趣的改编，比如把歌手写成 1920 年代舞厅里的钢琴师。
- 第二个问题：侧重哪方面的本事。从行动（格斗、射击、闪避）、调查（侦查、聆听、图书馆使用）、学识（历史、科学、神秘学）、社交（说服、魅惑、话术）里挑贴合本人的 3 个，可以写得更具体。
- 第三个问题由你根据这个人来定，比如性格的哪一面、什么年纪的他、和这个案子有什么牵连。
- 每个问题 2–4 个选项，每个选项不超过 24 字，使用简洁、正式的问句与平行选项，不模仿闲聊口吻。
- {RESPECT}""" + "\n\n" + INTERFACE_COPY

BUILD_SYSTEM = f"""你按《克苏鲁的呼唤》第 7 版的规则，把一位公众人物、一张角色卡里的人物，或者玩家自己的一句描述，做成这个模组的调查员。
玩家给的是描述时，名字由你起，符合模组的年代和地点。

- 身份：按玩家选的来。职业、住处、随身物品都要符合模组的年代和地点，不出现那个年代没有的东西。可以用本人的名字；改编成别的身份时，可以用符合年代的化名。
- 属性：把 80、70、60、60、50、50、50、40 分给力量、体质、体型、敏捷、外貌、智力、意志、教育，每个数用一次，按本人的特点分，比如运动员力量和敏捷高，学者智力和教育高。
- 职业技能点：挑最贴合职业的算法（{"、".join(FORMULAS)}），信用评级从这里出，其余分给八项左右的职业技能。
- 兴趣技能点：智力×2，分给任意技能。
- 填的是在基础值之上加的点，不是最终值。最拿手的技能最终在 60–80，最多 90。不要加克苏鲁神话。
- 技能名用这些，括号里的专长可以自己填，比如 艺术与手艺(钢琴)、外语(英语)、科学(物理)：{{skills}}
- 背景：个人描述、思想与信念、重要之人、意义非凡之地、宝贵之物、特质，各一两句，从本人的经历里取材，改编到模组的年代。
- 玩家提了要求就照做；规则不允许的，在 note 中说明具体限制。
- {RESPECT}""" + "\n\n" + INTERFACE_COPY


# -- looking them up ------------------------------------------------------------------
async def _json(client: httpx.AsyncClient, url: str, **params) -> dict:
    for attempt in range(3):
        r = await client.get(url, params=params or None)
        if r.status_code != 429 or attempt == 2:
            break
        await asyncio.sleep(min(float(r.headers.get("retry-after") or 1.5), 5.0))  # Wikipedia throttles bursts
    r.raise_for_status()
    return r.json()


async def _wikipedia(client: httpx.AsyncClient, fig: Figure) -> None:
    """The page by exactly that name (or a redirect to it), not one that merely mentions it."""
    for lang in ("zh", "ja", "en"):
        api = f"https://{lang}.wikipedia.org/w/api.php"
        q = await _json(client, api, action="query", titles=fig.query, redirects=1, converttitles=1, prop="langlinks", lllang="en", format="json")
        page = next(iter(q["query"]["pages"].values()))
        if "missing" in page or "invalid" in page:
            continue
        s = await _json(client, f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{page['title']}")
        if s.get("type") == "disambiguation":
            continue
        fig.title, fig.description, fig.extract = page["title"], s.get("description", ""), s.get("extract", "")[:1500]
        fig.page = s.get("content_urls", {}).get("desktop", {}).get("page", "")
        fig.english = page["title"] if lang == "en" else page.get("langlinks", [{}])[0].get("*", "")
        if image := (s.get("originalimage") or s.get("thumbnail") or {}).get("source"):
            fig.photos.insert(0, Photo(image, fig.page, f"维基百科《{fig.title}》"))
        return


def _excerpt(text: str, name: str, size: int = 1000) -> str:
    """The part of a page about them: from a little before the first mention of the name."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    at = text.find(name)
    return text[max(0, at - 100) : max(0, at - 100) + size] if at >= 0 else ""


async def _web(fig: Figure, provider: str) -> None:
    hits = await search.search(fig.query, provider, n=6)
    fig.web += [f"{h.title}（{h.url}）：{h.snippet}" for h in hits if h.snippet]
    pages = [h.url for h in hits if "wikipedia.org" not in h.url][:2]
    texts = await asyncio.gather(*(asyncio.wait_for(search.fetch(u, provider), 20) for u in pages), return_exceptions=True)
    fig.web += [f"{u}：{ex}" for u, t in zip(pages, texts, strict=True) if isinstance(t, str) and (ex := _excerpt(t, fig.query))]


async def _image_search(fig: Figure, query: str = "") -> None:
    """Photos from web image search (ddgs), larger ones first: by `query` when the person is known
    (Proposal.photo: 「哈姆」 alone found Hamtaro, the user, 2026-10-20), else by the name typed."""
    if not importlib.util.find_spec("ddgs"):
        return
    from ddgs import DDGS

    rows = await asyncio.to_thread(lambda: DDGS().images(query or fig.query, max_results=12))
    rows = [r for r in rows or [] if min(int(r.get("width") or 0), int(r.get("height") or 0)) >= 300]
    fig.photos += [Photo(r["image"], r.get("url", ""), r.get("title", "")[:40]) for r in rows[:6]]


async def research(query: str, provider: str = "auto", *, photos: bool = True) -> Figure:
    """Who they are and what they look like, from whatever the web has. `photos`: False when they
    are looked for once it is known who this is (find_photos, by the proposal's keywords)."""
    fig = Figure(query.strip())
    if resolve(provider) == "none":
        return fig
    headers = {"User-Agent": imagery.USER_AGENT, "Accept-Language": "zh-cn"}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        found = await asyncio.gather(_wikipedia(client, fig), _web(fig, provider), return_exceptions=True)
    del found  # a source that failed (offline, rate-limited, odd answer) just adds nothing
    if photos:
        await find_photos(fig)
    return fig


async def find_photos(fig: Figure, query: str = "") -> None:
    """Photo candidates: web image search by `query` (who this is, in a few words) or the name,
    then Commons by the English name or the page's title when that found few."""
    headers = {"User-Agent": imagery.USER_AGENT, "Accept-Language": "zh-cn"}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        try:
            await _image_search(fig, query)
        except Exception:  # offline, rate-limited: Commons may still have them
            pass
        if len(fig.photos) < 3 and (fig.english or fig.title):  # famous enough for Commons
            try:
                fig.photos += [Photo(c.thumb.replace("/360px-", "/800px-"), c.page, c.title, c.license) for c in await imagery.search(client, fig.english or fig.title, limit=8)]
            except httpx.HTTPError:
                pass
    seen: set[str] = set()
    fig.photos = [p for p in fig.photos if not (p.url in seen or seen.add(p.url))]


async def download(photo: Photo, dest: Path) -> bool:
    """One photo (on the web, or a file on this machine) saved at dest, at most 1024 px,
    with where it came from next to it."""
    try:
        if Path(photo.url).is_file():
            im = Image.open(photo.url).convert("RGB")
        else:
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": imagery.USER_AGENT}, follow_redirects=True) as client:
                r = await client.get(photo.url)
                r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
    except (httpx.HTTPError, OSError):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.thumbnail((1024, 1024))
    im.save(dest, quality=92)
    credits.put(dest, asdict(photo) | {"kind": "portrait"})
    return True


# -- cards the player brings -----------------------------------------------------------
ST_PAIR = re.compile(r"([^\d\s,，;；:：|/、()（）]+?)\s*[:：=]?\s*(\d{1,3})(?!\d)")
POOLS = {"理智": "san", "SAN": "san", "幸运": "luck", "运气": "luck", "LUCK": "luck", "HP": "hp", "体力": "hp", "生命": "hp", "MP": "mp", "魔法": "mp"}
KNOWN = {k.split("(")[0] for k in SKILL_BASE} | {"计算机使用", "电子学"}  # the modern-era ones too


def parse_st(text: str) -> dict | None:
    """The numbers of a dice bot's .st line ("力量60str60敏捷50…侦查60"), read by code rather
    than a model. None unless all eight characteristics are in it."""
    stats: dict[str, int] = {}
    skills: dict[str, int] = {}
    pools: dict[str, int] = {}
    for raw, num in ST_PAIR.findall(re.sub(r"[.。]st", " ", text, flags=re.I)):
        word = raw.strip(".。")
        if pool := POOLS.get(word) or POOLS.get(word.upper()):
            pools[pool] = int(num)
            continue
        key = canonical(word)
        if key in CHARACTERISTICS:
            stats[key] = int(num)
        elif key.split("(")[0] in KNOWN:
            skills[key] = int(num)
    return {"characteristics": stats, "skills": skills, **pools} if len(stats) == len(CHARACTERISTICS) else None


def is_description(text: str) -> bool:
    """A phrase about someone rather than a name: it says 的, lists traits, or runs long."""
    s = text.strip()
    return "的" in s or any(p in s for p in "，,、；;。") or len(s) > (24 if s.isascii() else 10)


def local_file(text: str) -> Path | None:
    """A file typed, pasted or dragged in (quoted, escaped, a file:// URI, a Windows path under WSL)."""
    p = sources.dropped_path(text)
    return p if p is not None and p.is_file() else None


def _tavern(data: object) -> dict | None:
    """The character in a SillyTavern card (spec v1, v2 or v3)."""
    if not isinstance(data, dict):
        return None
    body = data["data"] if isinstance(data.get("data"), dict) else data
    if not body.get("name") or not any(body.get(k) for k in ("description", "personality", "scenario")):
        return None
    name = str(body["name"]).strip()
    parts = [("人物", body.get("description")), ("性格", body.get("personality")), ("背景", body.get("scenario")), ("开场白", body.get("first_mes"))]
    text = "\n".join(f"{k}：{v.strip()}" for k, v in parts if isinstance(v, str) and v.strip())
    return {"name": name, "text": text.replace("{{char}}", name).replace("{{user}}", "你").replace("<START>", "")[:2500]}


def read_file(path: Path) -> tuple[dict | None, str, list[bytes] | None]:
    """(a character card's persona, the text of a sheet, an image of a sheet): one of them."""
    suffix = path.suffix.lower()
    if suffix in (".png", ".webp", ".jpg", ".jpeg"):
        with Image.open(path) as im:
            raw = im.info.get("ccv3") or im.info.get("chara")  # SillyTavern keeps the card in a text chunk
            if raw:
                try:
                    if persona := _tavern(json.loads(base64.b64decode(raw))):
                        return persona, "", None
                except (ValueError, binascii.Error):
                    pass
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
        return None, "", [buf.getvalue()]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return (persona, "", None) if (persona := _tavern(data)) else (None, json.dumps(data, ensure_ascii=False)[:12000], None)
    from markitdown import MarkItDown

    return None, MarkItDown().convert(str(path)).text_content[:12000], None


# -- the agent ------------------------------------------------------------------------
def _setting(m: Module) -> str:
    return f"【模组】《{m.title}》 {m.era} {m.setting}\n【开场】{m.hook.strip()[:400]}"


def describe(inv: Investigator) -> str:
    """A sheet in one paragraph, for the model and for the record."""
    stats = " ".join(f"{k}{v}" for k, v in inv.characteristics.items())
    skills = " ".join(f"{k}{v}" for k, v in inv.skills.items())
    story = "；".join(f"{k}：{v.rstrip('。')}" for k, v in inv.backstory.items())
    return f"{inv.name}，{inv.age} 岁，{inv.occupation}。属性：{stats}，幸运{inv.luck}。技能：{skills}。{story}。随身：{'、'.join(inv.inventory)}"


class Builder:
    """One card being made: the person, the answers, the requests, the latest sheet."""

    def __init__(self, cfg: Config, m: Module, *, luck: int | None = None):
        self.cfg, self.m = cfg, m
        self.id = f"card-{uuid.uuid4().hex[:6]}"
        self.luck = luck if luck is not None else sum(random.randint(1, 6) for _ in range(3)) * 5
        self.figure: Figure | None = None
        self.photo: Photo | None = None  # the one the player picked
        self.answers: list[tuple[str, str]] = []
        self.requests: list[str] = []
        self.card: Investigator | None = None
        self.fixes: list[str] = []
        self.note = ""
        self.kind = "figure"  # figure: built by the rules; sheet: a card brought as it was

    async def _ask_model(self, system: str, prompt: str, schema: type[BaseModel], *, images: list[bytes] | None = None):
        role = self.cfg.role("vision" if images else "card")
        s = Session("card", role, system, schema=schema.model_json_schema())
        try:
            return schema.model_validate((await s.send(prompt, images=images)).data or {})
        finally:
            await s.close()

    async def read(self, source: str, *, files: bool = True) -> str:
        """A card the player brings: pasted text or a file. "sheet" when it carries CoC numbers
        (self.card is ready); "persona" for a character without them, set up as self.figure
        to go through the questions like a public figure. `files`: False when the text came
        from someone else's machine, so a path in it is never opened here."""
        persona, text, images = None, source, None
        if files and (path := local_file(source)):
            persona, text, images = await asyncio.to_thread(read_file, path)
            if persona and images is None and path.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg"):
                self.figure = Figure(persona["name"], title=persona["name"], source="角色卡", extract=persona["text"], photos=[Photo(str(path), title="角色卡的图")])
                self.kind = "figure"
                return "persona"
        if persona:
            self.figure = Figure(persona["name"], title=persona["name"], source="角色卡", extract=persona["text"])
            self.kind = "figure"
            return "persona"
        numbers = parse_st(text) if text else None
        rest = ST_PAIR.sub("", re.sub(r"[.。]st", "", text or "", flags=re.I)).strip(" \n,，;；.。")
        sheet = Sheet(characteristics=Stats(**dict.fromkeys(CHARACTERISTICS, 0)))
        if images or numbers is None or len(rest) > 10:  # more than a bare .st line: names, a story, a scan
            skills = "、".join(k for k in SKILL_BASE)
            sheet = await self._ask_model(READ_SYSTEM.format(skills=skills), f"【原卡】\n{text or '（见图片）'}", Sheet, images=images)
        stats = numbers["characteristics"] if numbers else sheet.characteristics.model_dump()
        if not any(stats.values()):  # not a sheet at all: a character to build one for
            name = sheet.name or "这个角色"
            self.figure = Figure(name, title=name, source="人设", extract=(text or "")[:2500])
            self.kind = "figure"
            return "persona"
        skills_found = {**sheet.skills, **(numbers["skills"] if numbers else {})}
        found = numbers or {}  # what the .st line says stands over the model's reading, a 0 too; neither: None
        pools = {k: found[k] if k in found else getattr(sheet, k) for k in ("luck", "san", "hp")} | {"mp": found.get("mp")}
        self.card, self.fixes = accept(
            self.id, name=sheet.name or "无名调查员", occupation=sheet.occupation, age=sheet.age or 30, sex=sheet.sex,
            residence=sheet.residence, birthplace=sheet.birthplace, characteristics=stats, skills=skills_found,
            backstory={"说明": "玩家带来的人物卡"} | {k: v for k, v in sheet.backstory.items() if v}, inventory=sheet.inventory,
            portrait=self.id, **pools,
        )  # fmt: skip
        self.note = sheet.note or ("这张卡没有名字和职业，可以在“提要求修改”里补上" if not sheet.name else "")
        self.kind = "sheet"
        return "sheet"

    async def look_up(self, query: str) -> Figure:
        """A name is looked up on the web; a description ("在码头长大、胆子很大的女记者") is taken
        as the player's idea, with nothing to search for."""
        if is_description(query):
            self.figure = Figure(query.strip(), source="描述", extract=query.strip())
        else:
            self.figure = await research(query, self.cfg.search, photos=False)
        return self.figure

    async def propose(self) -> Proposal:
        """Who this is and the questions; then their photos, by the words that tell them from others
        of the same name (Proposal.photo)."""
        assert self.figure
        prompt = f"{_setting(self.m)}\n【这个人】{self.figure.brief()}"
        p = await self._ask_model(ASK_SYSTEM, prompt, Proposal)
        p.questions = [q for q in p.questions if q.options][:3]
        if self.figure.source != "描述" and resolve(self.cfg.search) != "none":
            await find_photos(self.figure, p.photo.strip())
        return p

    async def build(self, request: str = "") -> Investigator:
        """A new sheet from the answers and every request so far (the latest one included).
        A card the player brought is changed as asked instead, its numbers kept otherwise."""
        if self.kind == "sheet":
            return await self._revise(request)
        assert self.figure
        if request:
            self.requests.append(request)
        lines = [_setting(self.m), f"【这个人】{self.figure.brief()}"]
        lines += ["【玩家的选择】", *(f"- {q}：{a}" for q, a in self.answers)]
        if self.requests:
            lines += ["【玩家的要求】", *(f"- {r}" for r in self.requests)]
        if self.card:
            lines.append(f"【现在的卡】{describe(self.card)}")
        skills = "、".join(k for k in SKILL_BASE if k != "克苏鲁神话")
        d = await self._ask_model(BUILD_SYSTEM.format(skills=skills), "\n".join(lines), Draft)
        story = {"说明": f"AutoKeeper 根据{self.figure.name}改编"} | d.backstory.model_dump()
        card, self.fixes = make(
            self.id, name=d.name, occupation=d.occupation, age=d.age, characteristics=d.characteristics.model_dump(),
            formula=d.formula, credit=d.credit, occupation_skills=d.occupation_skills, interest_skills=d.interest_skills,
            luck=self.luck, sex=d.sex, residence=d.residence, birthplace=d.birthplace, portrait=self.id,
            backstory=story, inventory=d.inventory,
        )  # fmt: skip
        self.card, self.note = card, d.note
        return card

    async def _revise(self, request: str) -> Investigator:
        assert self.card
        if request:
            self.requests.append(request)
        c = self.card
        prompt = f"【现在的卡】{describe(c)}\n当前理智{c.san} HP{c.hp}\n【玩家的要求】{request}"
        d = await self._ask_model(REVISE_SYSTEM, prompt, Sheet)
        story = {"说明": c.backstory.get("说明", "玩家带来的人物卡")} | {k: v for k, v in d.backstory.items() if v and k != "说明"}
        # What the request leaves alone stays as it was, a 0 too, and MP with it (the sheet has none).
        card, self.fixes = accept(
            self.id, name=d.name or c.name, occupation=d.occupation or c.occupation, age=d.age or c.age, sex=d.sex or c.sex,
            residence=d.residence or c.residence, birthplace=d.birthplace or c.birthplace,
            characteristics=d.characteristics.model_dump(), skills=d.skills, luck=c.luck if d.luck is None else d.luck,
            san=c.san if d.san is None else d.san, hp=c.hp if d.hp is None else d.hp, mp=c.mp,
            backstory=story or c.backstory, inventory=d.inventory or c.inventory, portrait=self.id,
        )  # fmt: skip
        self.card, self.note = card, d.note
        return card

    async def find(self, query: str) -> Figure:
        """Photos for a card that came without one, by name (the card screen's 配一张照片)."""
        self.figure = await research(query, self.cfg.search)
        return self.figure

    async def download(self, i: int, dest: Path) -> bool:
        """Photo candidate `i` of the figure, saved at dest."""
        return bool(self.figure and 0 <= i < len(self.figure.photos)) and await download(self.figure.photos[i], dest)

    def keep(self, photo: Path | None) -> None:
        """The finished card into 我的角色, with the photo the player picked."""
        from .. import roster

        assert self.card
        roster.keep(self.card, self.record(), photo)

    def record(self) -> dict:
        """How the card was made, kept next to it."""
        fig = self.figure or Figure("")
        source = {"query": fig.query, "title": fig.title, "page": fig.page, "photo": asdict(self.photo) if self.photo else None}
        return {"figure": source, "answers": self.answers, "requests": self.requests, "module": self.m.id, "era": self.m.era}
