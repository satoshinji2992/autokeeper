"""Tools for tool-using agents, and the workspace they work in.

A tool is an async function with a name, a one-line description and the argument
fields it reads. The loop (loop.py) offers tools to any model through one flat JSON
step, so no provider tool-calling API is involved.

File tools work inside a Workspace: paths are relative to its root and cannot leave it,
and writes are allowed only where the writable patterns say.
"""

from __future__ import annotations

import inspect
import io
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from . import search as web

ARGS = {
    "path": "文件或目录的相对路径",
    "start": "从第几行（read_file）或第几个字（fetch_url）开始",
    "limit": "读多少行，默认 200，最多 400",
    "content": "要写入的完整文本",
    "old": "要替换的原文，必须在文件里只出现一次",
    "new": "替换成的新文本",
    "query": "搜索词（web_search）、正则表达式（grep）或想从图里看出什么（view_image）",
    "url": "网址",
}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class ToolError(Exception):
    """A mistake the model can fix; the message goes back to it as the observation."""


@dataclass
class Tool:
    name: str
    description: str
    args: tuple[str, ...]
    run: Callable[..., Awaitable[str]]
    required: tuple[str, ...] = ()


class Workspace:
    def __init__(self, root: Path, writable: Iterable[str] = ()):
        self.root = Path(root).resolve()
        self.writable = tuple(writable)

    def resolve(self, rel: str, *, write: bool = False) -> Path:
        p = (self.root / (rel or ".")).resolve()
        if not p.is_relative_to(self.root):
            raise ToolError(f"路径越界：{rel}（只能访问工作目录内的文件）")
        if write and not any(fnmatch(self.rel(p), pat) for pat in self.writable):
            raise ToolError(f"这里不能写：{rel}（可写：{'、'.join(self.writable) or '无'}）")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix() or "."


def file_tools(ws: Workspace) -> list[Tool]:
    async def ls(path: str = ".") -> str:
        p = ws.resolve(path)
        if not p.is_dir():
            raise ToolError(f"不是目录：{path}")
        rows = [c.name + "/" if c.is_dir() else f"{c.name}  {c.stat().st_size} 字节" for c in sorted(p.iterdir()) if not c.name.startswith(".")]
        return "\n".join(rows[:300]) or "（空目录）"

    async def read_file(path: str, start: int = 1, limit: int = 200) -> str:
        p = ws.resolve(path)
        if not p.is_file():
            raise ToolError(f"没有这个文件：{path}")
        if p.suffix.lower() in IMAGES:
            raise ToolError("这是图片，用 view_image 看")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start, limit = max(1, int(start or 1)), min(max(1, int(limit or 200)), 400)
        body = "\n".join(f"{i:>5}  {line[:1000]}" for i, line in enumerate(lines[start - 1 : start - 1 + limit], start))
        end = start - 1 + limit
        tail = f"（共 {len(lines)} 行，继续读用 start={end + 1}）" if end < len(lines) else f"（文件结束，共 {len(lines)} 行）"
        return f"{body}\n{tail}"

    async def grep(query: str, path: str = ".") -> str:
        try:
            rx = re.compile(query)
        except re.error as exc:
            raise ToolError(f"正则写错了：{exc}") from None
        base = ws.resolve(path)
        files = [base] if base.is_file() else sorted(f for f in base.rglob("*") if f.is_file())
        hits: list[str] = []
        for f in files:
            if f.suffix.lower() in IMAGES or f.stat().st_size > 4_000_000 or any(part.startswith(".") for part in f.parts):
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{ws.rel(f)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 60:
                        return "\n".join(hits) + "\n（只列前 60 处）"
        return "\n".join(hits) or "（没有匹配）"

    async def write_file(path: str, content: str) -> str:
        p = ws.resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {ws.rel(p)}：{content.count(chr(10)) + 1} 行，{len(content)} 字"

    async def append_file(path: str, content: str) -> str:
        p = ws.resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content if content.endswith("\n") else content + "\n")
        return f"已追加到 {ws.rel(p)}，现在共 {len(p.read_text(encoding='utf-8'))} 字"

    async def edit_file(path: str, old: str, new: str = "") -> str:
        p = ws.resolve(path, write=True)
        if not p.is_file():
            raise ToolError(f"没有这个文件：{path}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ToolError("没找到 old 这段原文；先用 read_file 或 grep 确认原文（空格、缩进、标点都要一致）")
        if count > 1:
            raise ToolError(f"old 这段原文出现了 {count} 次，多带几行上下文让它只出现一次")
        line = text[: text.index(old)].count("\n") + 1
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"已修改 {ws.rel(p)} 第 {line} 行起的一段"

    return [
        Tool("ls", "列出目录内容", ("path",), ls),
        Tool("read_file", "读文本文件，带行号，分段读", ("path", "start", "limit"), read_file, ("path",)),
        Tool("grep", "在文件里按正则搜索", ("query", "path"), grep, ("query",)),
        Tool("write_file", "新建或整个覆盖一个文件", ("path", "content"), write_file, ("path", "content")),
        Tool("append_file", "在文件末尾追加内容（写长文件或记笔记）", ("path", "content"), append_file, ("path", "content")),
        Tool("edit_file", "把文件里唯一的一段原文替换成新文本", ("path", "old", "new"), edit_file, ("path", "old")),
    ]


def image_tool(ws: Workspace, see: Callable[[bytes, str], Awaitable[str]]) -> Tool:
    """view_image: a vision model looks at the picture and answers in text, so the agent's
    own model does not need to read images."""

    async def view_image(path: str, query: str = "描述这张图") -> str:
        from PIL import Image

        p = ws.resolve(path)
        if not p.is_file() or p.suffix.lower() not in IMAGES:
            raise ToolError(f"不是图片：{path}")
        img = Image.open(p).convert("RGB")
        img.thumbnail((1568, 1568))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return await see(buf.getvalue(), query)

    return Tool("view_image", "让读图员看一张图片并回答你的问题", ("path", "query"), view_image, ("path",))


def check_tool(checker: Callable[[], object]) -> Tool:
    async def check() -> str:
        result = checker()
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    return Tool("check", "运行校验，列出还要修的问题", (), check)


def web_tools(provider: str) -> list[Tool]:
    off = "联网已关闭（search = none）。请根据已有资料和你自己的知识工作，查不到出处的事实写“未核实”。"

    async def web_search(query: str) -> str:
        try:
            hits = await web.search(query, provider)
        except web.SearchOff:
            return off
        except Exception as exc:  # network trouble is information for the agent, not a crash
            raise ToolError(f"搜索失败：{type(exc).__name__}: {exc}") from None
        return "\n".join(f"{i}. {h.title}\n   {h.url}\n   {h.snippet[:300]}" for i, h in enumerate(hits, 1)) or "（没有结果，换个说法再搜）"

    async def fetch_url(url: str, start: int = 0) -> str:
        try:
            text = await web.fetch(url, provider)
        except web.SearchOff:
            return off
        except Exception as exc:
            raise ToolError(f"打不开：{type(exc).__name__}: {exc}") from None
        start = max(0, int(start or 0))
        part = text[start : start + 6000]
        more = f"\n（共 {len(text)} 字，继续读用 start={start + 6000}）" if start + 6000 < len(text) else "\n（全文完）"
        return part + more

    return [
        Tool("web_search", "联网搜索，返回标题、网址和摘要", ("query",), web_search, ("query",)),
        Tool("fetch_url", "打开网页，转成 Markdown 文本分段读", ("url", "start"), fetch_url, ("url",)),
    ]
