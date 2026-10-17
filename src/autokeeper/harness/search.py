"""Web search and page fetching for agents. Offline is a first-class setting.

    auto       Tavily if TAVILY_API_KEY is set, else DuckDuckGo through ddgs, else Wikipedia
    tavily     Tavily search API (TAVILY_API_KEY)
    ddgs       the ddgs metasearch package, no key
    wikipedia  the MediaWiki search API (Chinese, then English Wikipedia)
    none       no network: tools say so, and agents work from what they already have

Pages are fetched and turned into Markdown with MarkItDown.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from dataclasses import dataclass

PROVIDERS = ("auto", "tavily", "ddgs", "wikipedia", "none")
UA = "AutoKeeper/0.2 (https://github.com/; TRPG research agent)"


class SearchOff(RuntimeError):
    pass


@dataclass
class Hit:
    title: str
    url: str
    snippet: str


def resolve(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"未知搜索后端：{provider}（可选：{', '.join(PROVIDERS)}）")
    if provider != "auto":
        return provider
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    return "ddgs" if importlib.util.find_spec("ddgs") else "wikipedia"


async def _tavily(query: str, n: int) -> list[Hit]:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": n},
            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
        )
        r.raise_for_status()
    return [Hit(x.get("title", ""), x.get("url", ""), x.get("content", "")) for x in r.json().get("results", [])]


async def _ddgs(query: str, n: int) -> list[Hit]:
    from ddgs import DDGS

    rows = await asyncio.to_thread(lambda: DDGS().text(query, max_results=n))
    return [Hit(x.get("title", ""), x.get("href", ""), x.get("body", "")) for x in rows or []]


async def _wikipedia(query: str, n: int) -> list[Hit]:
    import httpx

    hits: list[Hit] = []
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}) as client:
        for lang in ("zh", "en"):
            r = await client.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query, "srlimit": n, "format": "json"},
            )
            r.raise_for_status()
            for x in r.json().get("query", {}).get("search", []):
                url = f"https://{lang}.wikipedia.org/wiki/{x['title'].replace(' ', '_')}"
                hits.append(Hit(x["title"], url, re.sub(r"<[^>]+>", "", x.get("snippet", ""))))
            if hits:
                break
    return hits[:n]


async def search(query: str, provider: str = "auto", n: int = 6) -> list[Hit]:
    name = resolve(provider)
    if name == "none":
        raise SearchOff("联网搜索已关闭（search = none）")
    return await {"tavily": _tavily, "ddgs": _ddgs, "wikipedia": _wikipedia}[name](query, n)


async def fetch(url: str, provider: str = "auto") -> str:
    if resolve(provider) == "none":
        raise SearchOff("联网已关闭（search = none）")
    import httpx
    from markitdown import MarkItDown

    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as client:
        r = await client.get(url)
        r.raise_for_status()
    kind = r.headers.get("content-type", "").split(";")[0].strip()
    if kind.startswith("text/plain"):
        return r.text
    import io

    from markitdown import StreamInfo

    info = StreamInfo(mimetype=kind or None, url=str(r.url), charset=r.encoding)
    return await asyncio.to_thread(lambda: MarkItDown().convert_stream(io.BytesIO(r.content), stream_info=info).text_content)
